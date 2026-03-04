import asyncio
import io
import json
import logging
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
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException, status

try:
    import docker
except Exception as exc:  # noqa: BLE001
    raise RuntimeError("Docker SDK is not installed") from exc

from app.core.config import get_settings
from app.schemas.deployment import (
    DeploymentCreateRequest,
    DeploymentCreateResponse,
    DeploymentStatusResponse,
)

logger = logging.getLogger(__name__)


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
        logger.info(
            "[AI_SYS] DeploymentService initialized; entrypoint_rules=%s regex_rules=%s",
            len(self._entrypoint_filenames),
            len(self._entrypoint_regex_patterns),
        )

    async def request_deployment(
        self,
        payload: DeploymentCreateRequest,
        background_tasks: BackgroundTasks,
    ) -> DeploymentCreateResponse:
        logger.info(
            "[AI_SYS] Deployment request received tenant_id=%s github_url=%s",
            payload.tenant_id,
            payload.github_url,
        )
        if "github.com" not in payload.github_url.lower():
            logger.warning(
                "[AI_SYS] Deployment request rejected due to invalid github_url tenant_id=%s github_url=%s",
                payload.tenant_id,
                payload.github_url,
            )
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
        logger.info(
            "[AI_SYS] Deployment queued deployment_id=%s tenant_id=%s status=%s",
            deployment_id,
            payload.tenant_id,
            record.status,
        )
        return DeploymentCreateResponse(deployment_id=deployment_id, status="analyzing")

    def get_deployment_status(self, deployment_id: str, tenant_id: int) -> DeploymentStatusResponse:
        logger.debug(
            "[AI_SYS] Deployment status requested deployment_id=%s tenant_id=%s",
            deployment_id,
            tenant_id,
        )
        with self._lock:
            record = self._deployments.get(deployment_id)

        if not record or record.tenant_id != tenant_id:
            logger.warning(
                "[AI_SYS] Deployment status lookup failed deployment_id=%s tenant_id=%s",
                deployment_id,
                tenant_id,
            )
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
        logger.info(
            "[AI_SYS] Deployment delete requested deployment_id=%s tenant_id=%s",
            deployment_id,
            tenant_id,
        )
        with self._lock:
            record = self._deployments.get(deployment_id)
            if not record or record.tenant_id != tenant_id:
                logger.warning(
                    "[AI_SYS] Deployment delete failed (not found) deployment_id=%s tenant_id=%s",
                    deployment_id,
                    tenant_id,
                )
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
            record.cancel_requested = True
            record.status = "deleting"
            record.updated_at = datetime.utcnow()

        await asyncio.to_thread(self._cleanup_resources, record)
        with self._lock:
            current = self._deployments.get(deployment_id)
            if not current:
                logger.info(
                    "[AI_SYS] Deployment delete finished with no in-memory record deployment_id=%s",
                    deployment_id,
                )
                return
            current.status = "deleted"
            current.docker_image = None
            current.container_id = None
            current.container_name = None
            current.container_port = None
            current.public_url = None
            current.error_message = None
            current.updated_at = datetime.utcnow()
        logger.info(
            "[AI_SYS] Deployment delete completed deployment_id=%s tenant_id=%s",
            deployment_id,
            tenant_id,
        )

    async def _run_pipeline(self, deployment_id: str) -> None:
        with self._lock:
            record = self._deployments.get(deployment_id)
        if not record:
            logger.warning(
                "[AI_SYS] Deployment pipeline skipped; record missing deployment_id=%s",
                deployment_id,
            )
            return

        logger.info(
            "[AI_SYS] Deployment pipeline started deployment_id=%s tenant_id=%s github_url=%s",
            deployment_id,
            record.tenant_id,
            record.github_url,
        )
        try:
            self._assert_not_cancelled(deployment_id)
            with TemporaryDirectory(prefix=f"deploy-{record.tenant_id}-{deployment_id[:8]}-") as temp_dir:
                repo_dir = Path(temp_dir) / "repo"
                logger.info(
                    "[AI_SYS] Deployment temp workspace prepared deployment_id=%s path=%s",
                    deployment_id,
                    temp_dir,
                )

                self._update_deployment(deployment_id, status="cloning")
                await asyncio.to_thread(self._clone_repository, record.github_url, repo_dir)
                self._assert_not_cancelled(deployment_id)

                self._update_deployment(deployment_id, status="analyzing")
                context = await asyncio.to_thread(self._scan_repository, repo_dir)
                self._assert_not_cancelled(deployment_id)
                logger.info(
                    "[AI_SYS] Repository analyzed deployment_id=%s metadata_files=%s entrypoints=%s",
                    deployment_id,
                    len(context.metadata_files),
                    len(context.entrypoints),
                )

                self._update_deployment(deployment_id, status="generating_dockerfile")
                dockerfile = await asyncio.to_thread(self._generate_dockerfile, context)
                await asyncio.to_thread(
                    (repo_dir / "Dockerfile").write_text,
                    dockerfile,
                    "utf-8",
                )
                self._assert_not_cancelled(deployment_id)
                logger.info(
                    "[AI_SYS] Dockerfile generated deployment_id=%s bytes=%s",
                    deployment_id,
                    len(dockerfile.encode("utf-8", errors="ignore")),
                )

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
                logger.info(
                    "[AI_SYS] Container built and started deployment_id=%s image=%s container_id=%s name=%s port=%s",
                    deployment_id,
                    image_tag,
                    container_id,
                    container_name,
                    container_port,
                )

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
                logger.info(
                    "[AI_SYS] Deployment pipeline completed deployment_id=%s public_url=%s",
                    deployment_id,
                    public_url,
                )
        except DeploymentCancelledError:
            logger.warning(
                "[AI_SYS] Deployment cancelled deployment_id=%s",
                deployment_id,
            )
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
                logger.info(
                    "[AI_SYS] Cancelled deployment resources cleaned deployment_id=%s",
                    deployment_id,
                )
        except RepoNotFoundError as exc:
            logger.error(
                "[AI_SYS] Deployment failed repo_not_found deployment_id=%s github_url=%s error=%s",
                deployment_id,
                record.github_url,
                exc,
            )
            self._update_deployment(deployment_id, status="failed", error_message=f"Repo not found: {exc}")
        except BuildFailedError as exc:
            logger.error(
                "[AI_SYS] Deployment failed build_error deployment_id=%s error=%s",
                deployment_id,
                exc,
            )
            self._update_deployment(deployment_id, status="failed", error_message=f"Build failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "[AI_SYS] Deployment failed unexpected deployment_id=%s error=%s",
                deployment_id,
                exc,
            )
            self._update_deployment(deployment_id, status="failed", error_message=str(exc))

    def _update_deployment(self, deployment_id: str, **changes: object) -> None:
        with self._lock:
            record = self._deployments.get(deployment_id)
            if not record:
                logger.warning(
                    "[AI_SYS] Deployment update skipped; record missing deployment_id=%s changes=%s",
                    deployment_id,
                    list(changes.keys()),
                )
                return
            previous_status = record.status
            for key, value in changes.items():
                setattr(record, key, value)
            record.updated_at = datetime.utcnow()
            current_status = record.status
        if previous_status != current_status:
            logger.info(
                "[AI_SYS] Deployment status transition deployment_id=%s from=%s to=%s",
                deployment_id,
                previous_status,
                current_status,
            )
        elif changes:
            logger.debug(
                "[AI_SYS] Deployment fields updated deployment_id=%s fields=%s",
                deployment_id,
                list(changes.keys()),
            )

    def _assert_not_cancelled(self, deployment_id: str) -> None:
        with self._lock:
            record = self._deployments.get(deployment_id)
            if not record or record.cancel_requested:
                logger.warning(
                    "[AI_SYS] Deployment cancellation checkpoint hit deployment_id=%s record_exists=%s cancel_requested=%s",
                    deployment_id,
                    bool(record),
                    bool(record.cancel_requested) if record else False,
                )
                raise DeploymentCancelledError(deployment_id)

    def _clone_repository(self, github_url: str, destination: Path) -> None:
        logger.info(
            "[AI_SYS] Cloning repository url=%s destination=%s",
            github_url,
            destination,
        )
        command = ["git", "clone", "--depth", "1", github_url, str(destination)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            logger.info(
                "[AI_SYS] Repository cloned successfully url=%s destination=%s",
                github_url,
                destination,
            )
            return

        output = f"{result.stdout}\n{result.stderr}".lower()
        logger.error(
            "[AI_SYS] Repository clone failed url=%s return_code=%s stderr=%s",
            github_url,
            result.returncode,
            (result.stderr or "").strip(),
        )
        if "repository not found" in output or "not found" in output:
            raise RepoNotFoundError(github_url)
        if "could not read username" in output or "authentication failed" in output:
            raise RepoNotFoundError(github_url)
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git clone failed")

    def _scan_repository(self, repo_dir: Path) -> RepositoryContext:
        logger.info("[AI_SYS] Repository scan started path=%s", repo_dir)
        directory_tree = self._build_directory_tree(repo_dir)
        metadata_files: dict[str, str] = {}
        entrypoints: dict[str, str] = {}

        for filename in self.METADATA_FILES:
            target = repo_dir / filename
            if target.is_file():
                metadata_files[filename] = self._read_limited(target)
                logger.debug(
                    "[AI_SYS] Metadata file captured path=%s bytes=%s",
                    target,
                    len(metadata_files[filename].encode("utf-8", errors="ignore")),
                )

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
                logger.debug(
                    "[AI_SYS] Entrypoint captured relative_path=%s bytes=%s",
                    relative_path,
                    len(entrypoints[relative_path].encode("utf-8", errors="ignore")),
                )

        logger.info(
            "[AI_SYS] Repository scan completed path=%s metadata_count=%s entrypoint_count=%s",
            repo_dir,
            len(metadata_files),
            len(entrypoints),
        )
        return RepositoryContext(
            directory_tree=directory_tree,
            metadata_files=metadata_files,
            entrypoints=entrypoints,
        )

    def _build_directory_tree(self, repo_dir: Path) -> str:
        logger.debug("[AI_SYS] Building directory tree path=%s", repo_dir)
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
            logger.info(
                "[AI_SYS] Directory tree truncated path=%s max_entries=%s",
                repo_dir,
                self.MAX_TREE_ENTRIES,
            )
        logger.debug(
            "[AI_SYS] Directory tree built path=%s entries=%s",
            repo_dir,
            min(len(entries), self.MAX_TREE_ENTRIES),
        )
        return "\n".join(rendered)

    def _read_limited(self, path: Path) -> str:
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) <= self.MAX_FILE_CHARS:
            return content
        logger.info(
            "[AI_SYS] File content truncated path=%s max_chars=%s",
            path,
            self.MAX_FILE_CHARS,
        )
        return f"{content[: self.MAX_FILE_CHARS]}\n... (truncated)"

    def _load_entrypoint_rules(self) -> tuple[set[str], list[re.Pattern[str]]]:
        logger.info(
            "[AI_SYS] Loading entrypoint rules path=%s",
            self.ENTRYPOINT_CONFIG_PATH,
        )
        if not self.ENTRYPOINT_CONFIG_PATH.is_file():
            logger.error(
                "[AI_SYS] Entrypoint rules file missing path=%s",
                self.ENTRYPOINT_CONFIG_PATH,
            )
            raise RuntimeError(f"Entrypoint config file not found: {self.ENTRYPOINT_CONFIG_PATH}")

        try:
            raw_config = self.ENTRYPOINT_CONFIG_PATH.read_text(encoding="utf-8")
            parsed_config = json.loads(raw_config)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "[AI_SYS] Failed to parse entrypoint rules path=%s error=%s",
                self.ENTRYPOINT_CONFIG_PATH,
                exc,
            )
            raise RuntimeError("Failed to read entrypoint config file") from exc

        if not isinstance(parsed_config, dict):
            logger.error(
                "[AI_SYS] Entrypoint rules must be an object path=%s",
                self.ENTRYPOINT_CONFIG_PATH,
            )
            raise RuntimeError("entrypoint_rules.json must contain a JSON object")

        exact_filenames = parsed_config.get("exact_filenames", [])
        regex_patterns = parsed_config.get("regex_patterns", [])

        if not isinstance(exact_filenames, list) or any(not isinstance(item, str) for item in exact_filenames):
            logger.error("[AI_SYS] Entrypoint rules exact_filenames is invalid")
            raise RuntimeError("entrypoint_rules.json: exact_filenames must be a list of strings")
        if not isinstance(regex_patterns, list) or any(not isinstance(item, str) for item in regex_patterns):
            logger.error("[AI_SYS] Entrypoint rules regex_patterns is invalid")
            raise RuntimeError("entrypoint_rules.json: regex_patterns must be a list of strings")

        compiled_patterns: list[re.Pattern[str]] = []
        for pattern in regex_patterns:
            try:
                compiled_patterns.append(re.compile(pattern))
            except re.error as exc:
                logger.error(
                    "[AI_SYS] Entrypoint regex compile failed pattern=%s error=%s",
                    pattern,
                    exc,
                )
                raise RuntimeError(f"Invalid regex pattern in entrypoint_rules.json: {pattern}") from exc

        logger.info(
            "[AI_SYS] Entrypoint rules loaded exact=%s regex=%s",
            len(exact_filenames),
            len(compiled_patterns),
        )
        return set(exact_filenames), compiled_patterns

    def _matches_entrypoint_rule(self, filename: str, relative_path: str) -> bool:
        if filename in self._entrypoint_filenames:
            return True

        for pattern in self._entrypoint_regex_patterns:
            if pattern.search(filename) or pattern.search(relative_path):
                return True
        return False

    def _generate_dockerfile(self, context: RepositoryContext) -> str:
        if not self.settings.PROXYAPI_API_KEY:
            logger.error("[AI_SYS] PROXYAPI_API_KEY is missing")
            raise RuntimeError("PROXYAPI_API_KEY is not configured")

        logger.info(
            "[AI_SYS] ProxyAPI/OpenRouter Dockerfile generation started metadata_files=%s entrypoints=%s",
            len(context.metadata_files),
            len(context.entrypoints),
        )

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

        dockerfile_text = self._call_proxyapi_chat(prompt).strip()
        logger.info(
            "[AI_SYS] ProxyAPI/OpenRouter response received dockerfile_chars=%s",
            len(dockerfile_text),
        )

        if not dockerfile_text:
            logger.error("[AI_SYS] ProxyAPI/OpenRouter returned empty Dockerfile")
            raise RuntimeError("ProxyAPI/OpenRouter returned empty Dockerfile")
        if "```" in dockerfile_text:
            logger.error("[AI_SYS] ProxyAPI/OpenRouter returned markdown fences in Dockerfile response")
            raise RuntimeError("ProxyAPI/OpenRouter response must not include markdown code fences")
        if "FROM" not in dockerfile_text.upper():
            logger.error("[AI_SYS] ProxyAPI/OpenRouter Dockerfile validation failed: missing FROM")
            raise RuntimeError("ProxyAPI/OpenRouter returned invalid Dockerfile content")
        logger.info("[AI_SYS] ProxyAPI/OpenRouter Dockerfile validation succeeded")
        return dockerfile_text

    def _call_proxyapi_chat(self, prompt: str) -> str:
        base_url = self.settings.proxyapi_base_url.strip()
        if not base_url:
            raise RuntimeError("PROXYAPI_BASE_URL is not configured")

        endpoint = f"{base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.settings.proxyapi_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You generate Dockerfiles. Return only raw Dockerfile code without markdown or explanations."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "stream": False,
        }
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.settings.PROXYAPI_API_KEY}",
            },
        )
        logger.info(
            "[AI_SYS] ProxyAPI/OpenRouter request started endpoint=%s model=%s prompt_chars=%s timeout_sec=%s",
            endpoint,
            self.settings.proxyapi_model,
            len(prompt),
            self.settings.proxyapi_timeout_sec,
        )
        try:
            with urlopen(request, timeout=self.settings.proxyapi_timeout_sec) as response:
                response_bytes = response.read()
                logger.debug(
                    "[AI_SYS] ProxyAPI/OpenRouter response received status=%s bytes=%s",
                    getattr(response, "status", "unknown"),
                    len(response_bytes),
                )
                response_payload = json.loads(response_bytes.decode("utf-8"))
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            logger.error(
                "[AI_SYS] ProxyAPI/OpenRouter HTTP error status=%s body=%s",
                exc.code,
                error_body,
            )
            raise RuntimeError(f"ProxyAPI/OpenRouter HTTP error {exc.code}: {error_body}") from exc
        except URLError as exc:
            logger.error("[AI_SYS] ProxyAPI/OpenRouter connection error error=%s", exc)
            raise RuntimeError(f"ProxyAPI/OpenRouter connection error: {exc}") from exc
        except TimeoutError as exc:
            logger.error("[AI_SYS] ProxyAPI/OpenRouter timeout error=%s", exc)
            raise RuntimeError(f"ProxyAPI/OpenRouter timeout: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("[AI_SYS] ProxyAPI/OpenRouter unexpected error error=%s", exc)
            raise RuntimeError(f"ProxyAPI/OpenRouter request failed: {exc}") from exc

        return self._extract_proxyapi_text(response_payload)

    def _extract_proxyapi_text(self, response_payload: object) -> str:
        if not isinstance(response_payload, dict):
            logger.error("[AI_SYS] ProxyAPI/OpenRouter payload is not an object")
            raise RuntimeError("ProxyAPI/OpenRouter payload is invalid")

        error_payload = response_payload.get("error")
        if isinstance(error_payload, dict):
            message = str(error_payload.get("message") or "unknown error")
            logger.error("[AI_SYS] ProxyAPI/OpenRouter returned error payload message=%s", message)
            raise RuntimeError(f"ProxyAPI/OpenRouter error: {message}")

        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            logger.error("[AI_SYS] ProxyAPI/OpenRouter response has no choices")
            raise RuntimeError("ProxyAPI/OpenRouter response contains no choices")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            logger.error("[AI_SYS] ProxyAPI/OpenRouter first choice is invalid")
            raise RuntimeError("ProxyAPI/OpenRouter response choice is invalid")

        message = first_choice.get("message")
        if not isinstance(message, dict):
            logger.error("[AI_SYS] ProxyAPI/OpenRouter response message is invalid")
            raise RuntimeError("ProxyAPI/OpenRouter response message is invalid")

        content = message.get("content")
        if not isinstance(content, str):
            logger.error("[AI_SYS] ProxyAPI/OpenRouter response content is invalid")
            raise RuntimeError("ProxyAPI/OpenRouter response content is invalid")

        logger.debug("[AI_SYS] ProxyAPI/OpenRouter response text extracted successfully")
        return content

    def _build_and_run(self, repo_dir: Path, tenant_id: int, deployment_id: str) -> tuple[str, str, str, int]:
        image_tag = f"tenant-{tenant_id}-deploy-{deployment_id[:12]}".lower()
        container_name = f"deploy-{deployment_id[:12]}"
        logger.info(
            "[AI_SYS] Docker build started deployment_id=%s image_tag=%s repo_dir=%s",
            deployment_id,
            image_tag,
            repo_dir,
        )
        client = docker.from_env()
        try:
            try:
                client.images.build(path=str(repo_dir), tag=image_tag, rm=True, pull=True)
                logger.info(
                    "[AI_SYS] Docker build completed deployment_id=%s image_tag=%s",
                    deployment_id,
                    image_tag,
                )
            except docker.errors.BuildError as exc:
                logger.error(
                    "[AI_SYS] Docker build failed deployment_id=%s image_tag=%s error=%s",
                    deployment_id,
                    image_tag,
                    exc,
                )
                raise BuildFailedError(self._extract_build_error(exc)) from exc
            except docker.errors.APIError as exc:
                logger.error(
                    "[AI_SYS] Docker API error during build deployment_id=%s image_tag=%s error=%s",
                    deployment_id,
                    image_tag,
                    exc,
                )
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
            logger.info(
                "[AI_SYS] Container started deployment_id=%s container_id=%s container_name=%s container_port=%s",
                deployment_id,
                container.id,
                container_name,
                container_port,
            )
            return image_tag, container.id, container_name, container_port
        finally:
            client.close()
            logger.debug("[AI_SYS] Docker client closed after build/run deployment_id=%s", deployment_id)

    def _resolve_container_port(self, image: object) -> int:
        image_attrs = getattr(image, "attrs", {})
        exposed_ports = image_attrs.get("Config", {}).get("ExposedPorts") or {}
        if isinstance(exposed_ports, dict):
            for key in sorted(exposed_ports):
                match = re.match(r"^(\d+)/(tcp|udp)$", str(key))
                if match:
                    logger.info("[AI_SYS] Container port resolved from EXPOSE port=%s", match.group(1))
                    return int(match.group(1))
        logger.info("[AI_SYS] Container port fallback to default port=8000")
        return 8000

    def _ensure_network(self, client: object) -> None:
        networks = getattr(client, "networks", None)
        if networks is None:
            logger.error("[AI_SYS] Docker client networks API is unavailable")
            raise RuntimeError("Docker client networks API is unavailable")

        try:
            networks.get(self.settings.deployment_network_name)
            logger.debug(
                "[AI_SYS] Docker network exists name=%s",
                self.settings.deployment_network_name,
            )
        except docker.errors.NotFound:
            networks.create(self.settings.deployment_network_name, driver="bridge")
            logger.info(
                "[AI_SYS] Docker network created name=%s",
                self.settings.deployment_network_name,
            )

    def _configure_public_access(self, deployment_id: str, container_name: str, container_port: int) -> str | None:
        if not self.settings.domain:
            logger.warning(
                "[AI_SYS] Public access skipped because DOMAIN is not configured deployment_id=%s",
                deployment_id,
            )
            return None

        location_base = self._deployment_location_base(deployment_id)
        location_block = self._render_nginx_location_block(location_base, container_name, container_port)
        logger.info(
            "[AI_SYS] Configuring public access deployment_id=%s location_base=%s upstream=%s:%s",
            deployment_id,
            location_base,
            container_name,
            container_port,
        )

        client = docker.from_env()
        try:
            nginx_container = client.containers.get(self.settings.nginx_container_name)
            create_dir = nginx_container.exec_run(["mkdir", "-p", self.NGINX_DYNAMIC_DIR])
            if create_dir.exit_code != 0:
                logger.error(
                    "[AI_SYS] Failed to create nginx dynamic config directory deployment_id=%s output=%s",
                    deployment_id,
                    self._decode_exec_output(create_dir.output),
                )
                raise RuntimeError("Failed to prepare nginx dynamic config directory")

            config_name = f"{deployment_id}.conf"
            self._put_text_file(nginx_container, self.NGINX_DYNAMIC_DIR, config_name, location_block)
            self._reload_nginx(nginx_container)
        except docker.errors.NotFound as exc:
            logger.error(
                "[AI_SYS] Nginx container not found for public access deployment_id=%s container=%s",
                deployment_id,
                self.settings.nginx_container_name,
            )
            raise RuntimeError("Nginx container for deployment routing not found") from exc
        finally:
            client.close()
            logger.debug(
                "[AI_SYS] Docker client closed after public access configuration deployment_id=%s",
                deployment_id,
            )

        scheme = self.settings.deployment_public_scheme
        logger.info(
            "[AI_SYS] Public access configured deployment_id=%s url=%s://%s%s/",
            deployment_id,
            scheme,
            self.settings.domain,
            location_base,
        )
        return f"{scheme}://{self.settings.domain}{location_base}/"

    def _cleanup_resources(self, record: DeploymentRecord) -> None:
        logger.info(
            "[AI_SYS] Cleanup started deployment_id=%s container_id=%s container_name=%s image=%s",
            record.deployment_id,
            record.container_id,
            record.container_name,
            record.docker_image,
        )
        client = docker.from_env()
        try:
            if record.container_id:
                try:
                    container = client.containers.get(record.container_id)
                    container.remove(force=True, v=True)
                    logger.info(
                        "[AI_SYS] Container removed by id deployment_id=%s container_id=%s",
                        record.deployment_id,
                        record.container_id,
                    )
                except docker.errors.NotFound:
                    logger.warning(
                        "[AI_SYS] Container not found during cleanup by id deployment_id=%s container_id=%s",
                        record.deployment_id,
                        record.container_id,
                    )
            elif record.container_name:
                try:
                    container = client.containers.get(record.container_name)
                    container.remove(force=True, v=True)
                    logger.info(
                        "[AI_SYS] Container removed by name deployment_id=%s container_name=%s",
                        record.deployment_id,
                        record.container_name,
                    )
                except docker.errors.NotFound:
                    logger.warning(
                        "[AI_SYS] Container not found during cleanup by name deployment_id=%s container_name=%s",
                        record.deployment_id,
                        record.container_name,
                    )

            if record.docker_image:
                try:
                    client.images.remove(record.docker_image, force=True, noprune=False)
                    logger.info(
                        "[AI_SYS] Image removed deployment_id=%s image=%s",
                        record.deployment_id,
                        record.docker_image,
                    )
                except docker.errors.ImageNotFound:
                    logger.warning(
                        "[AI_SYS] Image not found during cleanup deployment_id=%s image=%s",
                        record.deployment_id,
                        record.docker_image,
                    )
                except docker.errors.APIError:
                    logger.warning(
                        "[AI_SYS] Docker API error removing image deployment_id=%s image=%s",
                        record.deployment_id,
                        record.docker_image,
                    )

            self._remove_public_access(record.deployment_id)
        finally:
            client.close()
            logger.info("[AI_SYS] Cleanup finished deployment_id=%s", record.deployment_id)

    def _remove_public_access(self, deployment_id: str) -> None:
        if not self.settings.domain:
            logger.info(
                "[AI_SYS] Public access removal skipped because DOMAIN is not configured deployment_id=%s",
                deployment_id,
            )
            return

        logger.info("[AI_SYS] Removing public access deployment_id=%s", deployment_id)
        client = docker.from_env()
        try:
            try:
                nginx_container = client.containers.get(self.settings.nginx_container_name)
            except docker.errors.NotFound:
                logger.warning(
                    "[AI_SYS] Nginx container not found while removing public access deployment_id=%s container=%s",
                    deployment_id,
                    self.settings.nginx_container_name,
                )
                return

            nginx_container.exec_run(["rm", "-f", f"{self.NGINX_DYNAMIC_DIR}/{deployment_id}.conf"])
            self._reload_nginx(nginx_container)
            logger.info("[AI_SYS] Public access removed deployment_id=%s", deployment_id)
        finally:
            client.close()
            logger.debug(
                "[AI_SYS] Docker client closed after removing public access deployment_id=%s",
                deployment_id,
            )

    def _reload_nginx(self, nginx_container: object) -> None:
        logger.debug("[AI_SYS] Nginx config test started")
        config_test = nginx_container.exec_run(["nginx", "-t"])
        if config_test.exit_code != 0:
            output = self._decode_exec_output(config_test.output)
            logger.error("[AI_SYS] Nginx config test failed output=%s", output)
            raise RuntimeError(f"nginx config test failed: {output}")

        logger.debug("[AI_SYS] Nginx reload started")
        reload_result = nginx_container.exec_run(["nginx", "-s", "reload"])
        if reload_result.exit_code != 0:
            output = self._decode_exec_output(reload_result.output)
            logger.error("[AI_SYS] Nginx reload failed output=%s", output)
            raise RuntimeError(f"nginx reload failed: {output}")
        logger.info("[AI_SYS] Nginx reload completed")

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
        logger.debug(
            "[AI_SYS] Deployment location base resolved deployment_id=%s prefix=%s",
            deployment_id,
            prefix,
        )
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
        logger.debug(
            "[AI_SYS] Writing file to container path=%s/%s bytes=%s",
            target_directory,
            filename,
            len(encoded),
        )
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            info = tarfile.TarInfo(name=filename)
            info.size = len(encoded)
            info.mtime = int(time.time())
            archive.addfile(info, io.BytesIO(encoded))
        stream.seek(0)
        container.put_archive(target_directory, stream.read())
        logger.debug("[AI_SYS] File written to container path=%s/%s", target_directory, filename)

    def _extract_build_error(self, exc: docker.errors.BuildError) -> str:
        if not exc.build_log:
            logger.debug("[AI_SYS] Build error has no build log")
            return str(exc)

        messages: list[str] = []
        for item in exc.build_log:
            if isinstance(item, dict):
                value = item.get("stream") or item.get("error")
                if value:
                    messages.append(str(value).strip())
        compact = " ".join(message for message in messages if message).strip()
        logger.debug("[AI_SYS] Build error extracted log_messages=%s", len(messages))
        return compact or str(exc)
