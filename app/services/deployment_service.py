import asyncio
import io
import json
import os
import re
import subprocess
import tarfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from urllib.parse import quote
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException, status

try:
    import docker
except Exception as exc:  # noqa: BLE001
    raise RuntimeError("Docker SDK is not installed") from exc

try:
    import google.generativeai as genai
except Exception as exc:  # noqa: BLE001
    raise RuntimeError("google-generativeai is not installed") from exc

from app.core.config import get_settings
from app.schemas.deployment import (
    DeploymentCreateRequest,
    DeploymentCreateResponse,
    DeploymentStatusResponse,
)


@dataclass
class DeploymentRecord:
    deployment_id: str
    tenant_id: int
    github_url: str
    status: str
    created_at: datetime
    updated_at: datetime
    docker_image: str | None = None
    container_id: str | None = None
    container_name: str | None = None
    container_port: int | None = None
    public_url: str | None = None
    error_message: str | None = None
    cancel_requested: bool = False


@dataclass
class RepositoryContext:
    directory_tree: str
    metadata_files: dict[str, str]
    entrypoints: dict[str, str]


class RepoNotFoundError(RuntimeError):
    """Raised when the GitHub repository cannot be cloned."""


class BuildFailedError(RuntimeError):
    """Raised when Docker image build fails."""


class DeploymentCancelledError(RuntimeError):
    """Raised when deployment has been cancelled by user."""


class DeploymentService:
    EXCLUDED_DIRS = {".git", "node_modules", "venv", ".venv"}
    METADATA_FILES = ("package.json", "requirements.txt", "pyproject.toml", "go.mod")
    ENTRYPOINT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "entrypoint_rules.json"
    NGINX_DYNAMIC_DIR = "/etc/nginx/deployment-locations"
    MAX_TREE_ENTRIES = 400
    MAX_FILE_CHARS = 8_000

    def __init__(self) -> None:
        self.settings = get_settings()
        self._entrypoint_filenames, self._entrypoint_regex_patterns = self._load_entrypoint_rules()
        self._deployments: dict[str, DeploymentRecord] = {}
        self._lock = Lock()

    async def request_deployment(
        self,
        payload: DeploymentCreateRequest,
        background_tasks: BackgroundTasks,
    ) -> DeploymentCreateResponse:
        if "github.com" not in payload.github_url.lower():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="github_url must be a GitHub URL",
            )

        deployment_id = uuid4().hex
        now = datetime.utcnow()
        record = DeploymentRecord(
            deployment_id=deployment_id,
            tenant_id=payload.tenant_id,
            github_url=payload.github_url,
            status="analyzing",
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._deployments[deployment_id] = record

        background_tasks.add_task(self._run_pipeline, deployment_id)
        return DeploymentCreateResponse(deployment_id=deployment_id, status="analyzing")

    def get_deployment_status(self, deployment_id: str, tenant_id: int) -> DeploymentStatusResponse:
        with self._lock:
            record = self._deployments.get(deployment_id)

        if not record or record.tenant_id != tenant_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")

        return DeploymentStatusResponse(
            deployment_id=record.deployment_id,
            tenant_id=record.tenant_id,
            github_url=record.github_url,
            status=record.status,
            docker_image=record.docker_image,
            container_id=record.container_id,
            container_name=record.container_name,
            container_port=record.container_port,
            public_url=record.public_url,
            error_message=record.error_message,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    async def delete_deployment(self, deployment_id: str, tenant_id: int) -> None:
        with self._lock:
            record = self._deployments.get(deployment_id)
            if not record or record.tenant_id != tenant_id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
            record.cancel_requested = True
            record.status = "deleting"
            record.updated_at = datetime.utcnow()

        await asyncio.to_thread(self._cleanup_resources, record)
        with self._lock:
            current = self._deployments.get(deployment_id)
            if not current:
                return
            current.status = "deleted"
            current.docker_image = None
            current.container_id = None
            current.container_name = None
            current.container_port = None
            current.public_url = None
            current.error_message = None
            current.updated_at = datetime.utcnow()

    async def _run_pipeline(self, deployment_id: str) -> None:
        with self._lock:
            record = self._deployments.get(deployment_id)
        if not record:
            return

        try:
            self._assert_not_cancelled(deployment_id)
            with TemporaryDirectory(prefix=f"deploy-{record.tenant_id}-{deployment_id[:8]}-") as temp_dir:
                repo_dir = Path(temp_dir) / "repo"

                self._update_deployment(deployment_id, status="cloning")
                await asyncio.to_thread(self._clone_repository, record.github_url, repo_dir)
                self._assert_not_cancelled(deployment_id)

                self._update_deployment(deployment_id, status="analyzing")
                context = await asyncio.to_thread(self._scan_repository, repo_dir)
                self._assert_not_cancelled(deployment_id)

                self._update_deployment(deployment_id, status="generating_dockerfile")
                dockerfile = await asyncio.to_thread(self._generate_dockerfile, context)
                await asyncio.to_thread(
                    (repo_dir / "Dockerfile").write_text,
                    dockerfile,
                    "utf-8",
                )
                self._assert_not_cancelled(deployment_id)

                self._update_deployment(deployment_id, status="building")
                image_tag, container_id, container_name, container_port = await asyncio.to_thread(
                    self._build_and_run,
                    repo_dir,
                    record.tenant_id,
                    deployment_id,
                )
                self._update_deployment(
                    deployment_id,
                    docker_image=image_tag,
                    container_id=container_id,
                    container_name=container_name,
                    container_port=container_port,
                )
                self._assert_not_cancelled(deployment_id)

                self._update_deployment(deployment_id, status="configuring_access")
                public_url = await asyncio.to_thread(
                    self._configure_public_access,
                    deployment_id,
                    container_name,
                    container_port,
                )
                self._assert_not_cancelled(deployment_id)
                self._update_deployment(
                    deployment_id,
                    status="running",
                    public_url=public_url,
                    error_message=None,
                )
        except DeploymentCancelledError:
            with self._lock:
                cancelled_record = self._deployments.get(deployment_id)
            if cancelled_record:
                await asyncio.to_thread(self._cleanup_resources, cancelled_record)
                with self._lock:
                    current = self._deployments.get(deployment_id)
                    if current:
                        current.status = "deleted"
                        current.docker_image = None
                        current.container_id = None
                        current.container_name = None
                        current.container_port = None
                        current.public_url = None
                        current.error_message = None
                        current.updated_at = datetime.utcnow()
        except RepoNotFoundError as exc:
            self._update_deployment(deployment_id, status="failed", error_message=f"Repo not found: {exc}")
        except BuildFailedError as exc:
            self._update_deployment(deployment_id, status="failed", error_message=f"Build failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            self._update_deployment(deployment_id, status="failed", error_message=str(exc))

    def _update_deployment(self, deployment_id: str, **changes: object) -> None:
        with self._lock:
            record = self._deployments.get(deployment_id)
            if not record:
                return
            for key, value in changes.items():
                setattr(record, key, value)
            record.updated_at = datetime.utcnow()

    def _assert_not_cancelled(self, deployment_id: str) -> None:
        with self._lock:
            record = self._deployments.get(deployment_id)
            if not record or record.cancel_requested:
                raise DeploymentCancelledError(deployment_id)

    def _clone_repository(self, github_url: str, destination: Path) -> None:
        command = ["git", "clone", "--depth", "1", github_url, str(destination)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return

        output = f"{result.stdout}\n{result.stderr}".lower()
        if "repository not found" in output or "not found" in output:
            raise RepoNotFoundError(github_url)
        if "could not read username" in output or "authentication failed" in output:
            raise RepoNotFoundError(github_url)
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git clone failed")

    def _scan_repository(self, repo_dir: Path) -> RepositoryContext:
        directory_tree = self._build_directory_tree(repo_dir)
        metadata_files: dict[str, str] = {}
        entrypoints: dict[str, str] = {}

        for filename in self.METADATA_FILES:
            target = repo_dir / filename
            if target.is_file():
                metadata_files[filename] = self._read_limited(target)

        for root, dirs, files in os.walk(repo_dir):
            dirs[:] = sorted(directory for directory in dirs if directory not in self.EXCLUDED_DIRS)
            files = sorted(files)
            root_path = Path(root)
            for filename in files:
                file_path = root_path / filename
                relative_path = file_path.relative_to(repo_dir).as_posix()
                if not self._matches_entrypoint_rule(filename, relative_path):
                    continue
                entrypoints[relative_path] = self._read_limited(file_path)

        return RepositoryContext(
            directory_tree=directory_tree,
            metadata_files=metadata_files,
            entrypoints=entrypoints,
        )

    def _build_directory_tree(self, repo_dir: Path) -> str:
        entries: list[str] = []
        truncated = False
        for root, dirs, files in os.walk(repo_dir):
            dirs[:] = sorted(directory for directory in dirs if directory not in self.EXCLUDED_DIRS)
            files = sorted(files)
            root_path = Path(root)

            for directory in dirs:
                path = root_path / directory
                relative = path.relative_to(repo_dir).as_posix()
                entries.append(f"{relative}/")

            for file_name in files:
                path = root_path / file_name
                relative = path.relative_to(repo_dir).as_posix()
                entries.append(relative)

            if len(entries) >= self.MAX_TREE_ENTRIES:
                truncated = True
                break

        rendered = ["./"]
        for entry in entries[: self.MAX_TREE_ENTRIES]:
            depth = max(len(Path(entry.rstrip("/")).parts) - 1, 0)
            indent = "  " * depth
            name = entry.rstrip("/").split("/")[-1]
            suffix = "/" if entry.endswith("/") else ""
            rendered.append(f"{indent}{name}{suffix}")

        if truncated:
            rendered.append("  ... (truncated)")
        return "\n".join(rendered)

    def _read_limited(self, path: Path) -> str:
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) <= self.MAX_FILE_CHARS:
            return content
        return f"{content[: self.MAX_FILE_CHARS]}\n... (truncated)"

    def _load_entrypoint_rules(self) -> tuple[set[str], list[re.Pattern[str]]]:
        if not self.ENTRYPOINT_CONFIG_PATH.is_file():
            raise RuntimeError(f"Entrypoint config file not found: {self.ENTRYPOINT_CONFIG_PATH}")

        try:
            raw_config = self.ENTRYPOINT_CONFIG_PATH.read_text(encoding="utf-8")
            parsed_config = json.loads(raw_config)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Failed to read entrypoint config file") from exc

        if not isinstance(parsed_config, dict):
            raise RuntimeError("entrypoint_rules.json must contain a JSON object")

        exact_filenames = parsed_config.get("exact_filenames", [])
        regex_patterns = parsed_config.get("regex_patterns", [])

        if not isinstance(exact_filenames, list) or any(not isinstance(item, str) for item in exact_filenames):
            raise RuntimeError("entrypoint_rules.json: exact_filenames must be a list of strings")
        if not isinstance(regex_patterns, list) or any(not isinstance(item, str) for item in regex_patterns):
            raise RuntimeError("entrypoint_rules.json: regex_patterns must be a list of strings")

        compiled_patterns: list[re.Pattern[str]] = []
        for pattern in regex_patterns:
            try:
                compiled_patterns.append(re.compile(pattern))
            except re.error as exc:
                raise RuntimeError(f"Invalid regex pattern in entrypoint_rules.json: {pattern}") from exc

        return set(exact_filenames), compiled_patterns

    def _matches_entrypoint_rule(self, filename: str, relative_path: str) -> bool:
        if filename in self._entrypoint_filenames:
            return True

        for pattern in self._entrypoint_regex_patterns:
            if pattern.search(filename) or pattern.search(relative_path):
                return True
        return False

    def _generate_dockerfile(self, context: RepositoryContext) -> str:
        if not self.settings.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        self._configure_gemini_proxy()
        genai.configure(api_key=self.settings.GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-flash-latest")

        prompt_context = {
            "directory_tree": context.directory_tree,
            "metadata_files": context.metadata_files,
            "entrypoint_files": context.entrypoints,
        }

        prompt = (
            "Generate a production-ready Dockerfile for this repository.\n"
            "Output rules:\n"
            "1. Return ONLY raw Dockerfile code.\n"
            "2. Do not add markdown, comments outside Dockerfile, or explanations.\n"
            "3. Do not wrap output with triple backticks.\n\n"
            "Dockerfile goals:\n"
            "- Use an appropriate official base image.\n"
            "- Install dependencies from repository metadata.\n"
            "- Add a reliable startup command.\n"
            "- Use production settings and keep image reasonably small.\n"
            "- Expose a typical app port when identifiable.\n\n"
            "Repository context:\n"
            f"{json.dumps(prompt_context, ensure_ascii=True, indent=2)}"
        )

        response = model.generate_content(prompt)
        dockerfile_text = self._extract_text(response).strip()

        if not dockerfile_text:
            raise RuntimeError("Gemini returned empty Dockerfile")
        if "```" in dockerfile_text:
            raise RuntimeError("Gemini response must not include markdown code fences")
        if "FROM" not in dockerfile_text.upper():
            raise RuntimeError("Gemini returned invalid Dockerfile content")
        return dockerfile_text

    def _configure_gemini_proxy(self) -> None:
        proxy_url = self._build_gemini_proxy_url()
        if not proxy_url:
            return

        os.environ["HTTP_PROXY"] = proxy_url
        os.environ["HTTPS_PROXY"] = proxy_url
        os.environ["http_proxy"] = proxy_url
        os.environ["https_proxy"] = proxy_url

    def _build_gemini_proxy_url(self) -> str | None:
        direct_proxy_url = self.settings.gemini_proxy_url.strip()
        if direct_proxy_url:
            return direct_proxy_url

        proxy_host = self.settings.gemini_proxy_host.strip()
        if not proxy_host:
            return None

        proxy_scheme = self.settings.gemini_proxy_scheme.strip() or "http"
        proxy_port = self.settings.gemini_proxy_port.strip()
        proxy_username = self.settings.gemini_proxy_username
        proxy_password = self.settings.gemini_proxy_password

        auth_part = ""
        if proxy_username and not proxy_password:
            raise RuntimeError("GEMINI proxy password is required when username is configured")
        if proxy_password and not proxy_username:
            raise RuntimeError("GEMINI proxy username is required when password is configured")
        if proxy_username:
            encoded_username = quote(proxy_username, safe="")
            encoded_password = quote(proxy_password, safe="")
            auth_part = f"{encoded_username}:{encoded_password}@"

        port_part = f":{proxy_port}" if proxy_port else ""
        return f"{proxy_scheme}://{auth_part}{proxy_host}{port_part}"

    def _extract_text(self, response: object) -> str:
        text = getattr(response, "text", None)
        if text:
            return str(text)

        candidates = getattr(response, "candidates", []) or []
        chunks: list[str] = []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            if not content:
                continue
            parts = getattr(content, "parts", []) or []
            for part in parts:
                part_text = getattr(part, "text", "")
                if part_text:
                    chunks.append(str(part_text))
        return "\n".join(chunks)

    def _build_and_run(self, repo_dir: Path, tenant_id: int, deployment_id: str) -> tuple[str, str, str, int]:
        image_tag = f"tenant-{tenant_id}-deploy-{deployment_id[:12]}".lower()
        container_name = f"deploy-{deployment_id[:12]}"
        client = docker.from_env()
        try:
            try:
                client.images.build(path=str(repo_dir), tag=image_tag, rm=True, pull=True)
            except docker.errors.BuildError as exc:
                raise BuildFailedError(self._extract_build_error(exc)) from exc
            except docker.errors.APIError as exc:
                raise BuildFailedError(str(exc)) from exc

            image = client.images.get(image_tag)
            container_port = self._resolve_container_port(image)
            self._ensure_network(client)
            container = client.containers.run(
                image=image_tag,
                detach=True,
                mem_limit="512m",
                network=self.settings.deployment_network_name,
                labels={
                    "tenant_id": str(tenant_id),
                    "deployment_id": deployment_id,
                },
                name=container_name,
                restart_policy={"Name": "unless-stopped"},
            )
            return image_tag, container.id, container_name, container_port
        finally:
            client.close()

    def _resolve_container_port(self, image: object) -> int:
        image_attrs = getattr(image, "attrs", {})
        exposed_ports = image_attrs.get("Config", {}).get("ExposedPorts") or {}
        if isinstance(exposed_ports, dict):
            for key in sorted(exposed_ports):
                match = re.match(r"^(\d+)/(tcp|udp)$", str(key))
                if match:
                    return int(match.group(1))
        return 8000

    def _ensure_network(self, client: object) -> None:
        networks = getattr(client, "networks", None)
        if networks is None:
            raise RuntimeError("Docker client networks API is unavailable")

        try:
            networks.get(self.settings.deployment_network_name)
        except docker.errors.NotFound:
            networks.create(self.settings.deployment_network_name, driver="bridge")

    def _configure_public_access(self, deployment_id: str, container_name: str, container_port: int) -> str | None:
        if not self.settings.domain:
            return None

        location_base = self._deployment_location_base(deployment_id)
        location_block = self._render_nginx_location_block(location_base, container_name, container_port)

        client = docker.from_env()
        try:
            nginx_container = client.containers.get(self.settings.nginx_container_name)
            create_dir = nginx_container.exec_run(["mkdir", "-p", self.NGINX_DYNAMIC_DIR])
            if create_dir.exit_code != 0:
                raise RuntimeError("Failed to prepare nginx dynamic config directory")

            config_name = f"{deployment_id}.conf"
            self._put_text_file(nginx_container, self.NGINX_DYNAMIC_DIR, config_name, location_block)
            self._reload_nginx(nginx_container)
        except docker.errors.NotFound as exc:
            raise RuntimeError("Nginx container for deployment routing not found") from exc
        finally:
            client.close()

        scheme = self.settings.deployment_public_scheme
        return f"{scheme}://{self.settings.domain}{location_base}/"

    def _cleanup_resources(self, record: DeploymentRecord) -> None:
        client = docker.from_env()
        try:
            if record.container_id:
                try:
                    container = client.containers.get(record.container_id)
                    container.remove(force=True, v=True)
                except docker.errors.NotFound:
                    pass
            elif record.container_name:
                try:
                    container = client.containers.get(record.container_name)
                    container.remove(force=True, v=True)
                except docker.errors.NotFound:
                    pass

            if record.docker_image:
                try:
                    client.images.remove(record.docker_image, force=True, noprune=False)
                except docker.errors.ImageNotFound:
                    pass
                except docker.errors.APIError:
                    pass

            self._remove_public_access(record.deployment_id)
        finally:
            client.close()

    def _remove_public_access(self, deployment_id: str) -> None:
        if not self.settings.domain:
            return

        client = docker.from_env()
        try:
            try:
                nginx_container = client.containers.get(self.settings.nginx_container_name)
            except docker.errors.NotFound:
                return

            nginx_container.exec_run(["rm", "-f", f"{self.NGINX_DYNAMIC_DIR}/{deployment_id}.conf"])
            self._reload_nginx(nginx_container)
        finally:
            client.close()

    def _reload_nginx(self, nginx_container: object) -> None:
        config_test = nginx_container.exec_run(["nginx", "-t"])
        if config_test.exit_code != 0:
            output = self._decode_exec_output(config_test.output)
            raise RuntimeError(f"nginx config test failed: {output}")

        reload_result = nginx_container.exec_run(["nginx", "-s", "reload"])
        if reload_result.exit_code != 0:
            output = self._decode_exec_output(reload_result.output)
            raise RuntimeError(f"nginx reload failed: {output}")

    def _decode_exec_output(self, output: bytes | tuple[bytes | None, bytes | None] | None) -> str:
        if output is None:
            return ""
        if isinstance(output, tuple):
            stdout = (output[0] or b"").decode("utf-8", errors="replace")
            stderr = (output[1] or b"").decode("utf-8", errors="replace")
            return f"{stdout}\n{stderr}".strip()
        if isinstance(output, bytes):
            return output.decode("utf-8", errors="replace").strip()
        return str(output).strip()

    def _deployment_location_base(self, deployment_id: str) -> str:
        prefix = self.settings.deployment_public_path_prefix.strip("/")
        if not prefix:
            prefix = "hosted"
        return f"/{prefix}/{deployment_id}"

    def _render_nginx_location_block(self, location_base: str, container_name: str, container_port: int) -> str:
        return (
            f"location = {location_base} {{\n"
            f"    return 301 {location_base}/;\n"
            "}\n\n"
            f"location ^~ {location_base}/ {{\n"
            f"    proxy_pass http://{container_name}:{container_port}/;\n"
            "    proxy_http_version 1.1;\n"
            "    proxy_set_header Host $host;\n"
            "    proxy_set_header X-Real-IP $remote_addr;\n"
            "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
            "    proxy_set_header X-Forwarded-Proto $scheme;\n"
            "    proxy_set_header Upgrade $http_upgrade;\n"
            "    proxy_set_header Connection \"upgrade\";\n"
            "}\n"
        )

    def _put_text_file(
        self,
        container: object,
        target_directory: str,
        filename: str,
        content: str,
    ) -> None:
        encoded = content.encode("utf-8")
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            info = tarfile.TarInfo(name=filename)
            info.size = len(encoded)
            info.mtime = int(time.time())
            archive.addfile(info, io.BytesIO(encoded))
        stream.seek(0)
        container.put_archive(target_directory, stream.read())

    def _extract_build_error(self, exc: docker.errors.BuildError) -> str:
        if not exc.build_log:
            return str(exc)

        messages: list[str] = []
        for item in exc.build_log:
            if isinstance(item, dict):
                value = item.get("stream") or item.get("error")
                if value:
                    messages.append(str(value).strip())
        compact = " ".join(message for message in messages if message).strip()
        return compact or str(exc)
