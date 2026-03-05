import asyncio
import fnmatch
import io
import json
import logging
import os
import re
import socket
import subprocess
import tarfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException, status
from sqlmodel import Session, select

try:
    import docker
except Exception as exc:  # noqa: BLE001
    raise RuntimeError("Docker SDK is not installed") from exc

from app.core.config import get_settings
from app.db.session import engine
from app.models import Deployment as DeploymentModel
from app.models import DeploymentAttempt as DeploymentAttemptModel
from app.schemas.deployment import (
    DeploymentAttemptRead,
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
    current_attempt: int = 0
    max_attempts: int = 3
    attempts: list["DeploymentAttemptRecord"] = field(default_factory=list)
    cancel_requested: bool = False


@dataclass
class RepositoryContext:
    directory_tree: str
    metadata_files: dict[str, str]
    entrypoints: dict[str, str]


@dataclass
class DeploymentAttemptRecord:
    attempt: int
    status: str
    technology: str | None
    dockerfile: str | None
    build_error: str | None
    prompt_context_chars: int
    started_at: datetime
    finished_at: datetime | None


class RepoNotFoundError(RuntimeError):
    """Raised when the GitHub repository cannot be cloned."""


class BuildFailedError(RuntimeError):
    """Raised when Docker image build fails."""


class DeploymentCancelledError(RuntimeError):
    """Raised when deployment has been cancelled by user."""


class DeploymentService:
    EXCLUDED_DIRS = {".git", "node_modules", "venv", ".venv"}
    METADATA_FILES = ("package.json", "requirements.txt", "pyproject.toml", "go.mod")
    COMMON_BUILD_FILES = {
        "Dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "docker-compose.dev.yml",
        "docker-compose.prod.yml",
        ".dockerignore",
        "Makefile",
        "Procfile",
        ".env.example",
    }
    TECHNOLOGY_SIGNATURES: dict[str, tuple[str, ...]] = {
        "dotnet": (
            ".sln",
            ".csproj",
            ".fsproj",
            "Directory.Build.props",
            "Directory.Packages.props",
            "global.json",
            "NuGet.config",
            "nuget.config",
        ),
        "python": ("requirements.txt", "pyproject.toml", "setup.py", "Pipfile"),
        "node": ("package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json"),
        "java": ("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle"),
        "go": ("go.mod",),
        "rust": ("Cargo.toml",),
        "php": ("composer.json",),
        "ruby": ("Gemfile",),
    }
    TECHNOLOGY_BUILD_FILES: dict[str, tuple[str, ...]] = {
        "dotnet": (
            "*.sln",
            "*.csproj",
            "*.fsproj",
            "Directory.Build.props",
            "Directory.Packages.props",
            "global.json",
            "NuGet.config",
            "nuget.config",
        ),
        "python": (
            "requirements*.txt",
            "pyproject.toml",
            "Pipfile",
            "Pipfile.lock",
            "setup.py",
            "poetry.lock",
            "uv.lock",
            "gunicorn.conf.py",
            "manage.py",
            "wsgi.py",
            "asgi.py",
        ),
        "node": (
            "package.json",
            "package-lock.json",
            "yarn.lock",
            "pnpm-lock.yaml",
            "npm-shrinkwrap.json",
            "next.config.*",
            "vite.config.*",
            "nuxt.config.*",
            "svelte.config.*",
        ),
        "java": (
            "pom.xml",
            "build.gradle",
            "build.gradle.kts",
            "settings.gradle",
            "settings.gradle.kts",
            "gradle.properties",
            "mvnw",
            "gradlew",
        ),
        "go": ("go.mod", "go.sum", "*.go"),
        "rust": ("Cargo.toml", "Cargo.lock", "*.rs"),
        "php": ("composer.json", "composer.lock", "*.php"),
        "ruby": ("Gemfile", "Gemfile.lock", "config.ru", "Rakefile"),
    }
    ENTRYPOINT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "entrypoint_rules.json"
    NGINX_DYNAMIC_DIR = "/etc/nginx/deployment-servers"
    LEGACY_NGINX_DYNAMIC_DIR = "/etc/nginx/deployment-locations"
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
        logger.info(
            "[AI_SYS] DeploymentService retry settings max_attempts=%s retry_context_max_chars=%s",
            self._effective_max_attempts(),
            self._effective_retry_context_max_chars(),
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
        max_attempts = self._effective_max_attempts()
        record = DeploymentRecord(
            deployment_id=deployment_id,
            tenant_id=payload.tenant_id,
            github_url=payload.github_url,
            status="analyzing",
            created_at=now,
            updated_at=now,
            max_attempts=max_attempts,
        )
        with self._lock:
            self._deployments[deployment_id] = record
        self._persist_deployment_state(deployment_id)

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
        record = self._get_or_load_record_for_tenant(deployment_id=deployment_id, tenant_id=tenant_id)
        if not record:
            logger.warning(
                "[AI_SYS] Deployment status lookup failed deployment_id=%s tenant_id=%s",
                deployment_id,
                tenant_id,
            )
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
        return self._record_to_status_response(record)

    def list_deployments(
        self,
        tenant_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DeploymentStatusResponse]:
        records = self._list_deployments_from_db(
            tenant_id=tenant_id,
            limit=limit,
            offset=offset,
        )
        if not records:
            with self._lock:
                memory_records = [
                    self._clone_deployment_record(item)
                    for item in self._deployments.values()
                    if item.tenant_id == tenant_id
                ]
            memory_records.sort(key=lambda item: item.created_at, reverse=True)
            records = memory_records[offset : offset + limit]
        return [self._record_to_status_response(item) for item in records]

    async def delete_deployment(self, deployment_id: str, tenant_id: int) -> None:
        logger.info(
            "[AI_SYS] Deployment delete requested deployment_id=%s tenant_id=%s",
            deployment_id,
            tenant_id,
        )
        record = self._get_or_load_record_for_tenant(deployment_id=deployment_id, tenant_id=tenant_id)
        if not record:
            logger.warning(
                "[AI_SYS] Deployment delete failed (not found) deployment_id=%s tenant_id=%s",
                deployment_id,
                tenant_id,
            )
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")

        with self._lock:
            current = self._deployments.get(deployment_id)
            if not current:
                self._deployments[deployment_id] = record
                current = record
            current.cancel_requested = True
            current.status = "deleting"
            current.updated_at = datetime.utcnow()
            cleanup_record = self._clone_deployment_record(current)
        self._persist_deployment_state(deployment_id)

        await asyncio.to_thread(self._cleanup_resources, cleanup_record)
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
        self._persist_deployment_state(deployment_id)
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
                technology = await asyncio.to_thread(self._detect_technology, repo_dir, context)
                self._assert_not_cancelled(deployment_id)
                logger.info(
                    "[AI_SYS] Repository analyzed deployment_id=%s metadata_files=%s entrypoints=%s technology=%s",
                    deployment_id,
                    len(context.metadata_files),
                    len(context.entrypoints),
                    technology or "unknown",
                )
                self._update_deployment(deployment_id, current_attempt=0, error_message=None)

                max_attempts = self._get_max_attempts(deployment_id)
                for attempt_number in range(1, max_attempts + 1):
                    self._assert_not_cancelled(deployment_id)
                    self._start_attempt(deployment_id, attempt_number, technology)
                    logger.info(
                        "[AI_SYS] Deployment attempt started deployment_id=%s attempt=%s/%s technology=%s",
                        deployment_id,
                        attempt_number,
                        max_attempts,
                        technology or "unknown",
                    )

                    try:
                        self._update_deployment(
                            deployment_id,
                            status="generating_dockerfile",
                            current_attempt=attempt_number,
                        )
                        prompt_context, prompt_context_chars = await asyncio.to_thread(
                            self._build_prompt_context,
                            repo_dir,
                            context,
                            technology,
                            attempt_number,
                            deployment_id,
                        )
                        self._update_attempt(
                            deployment_id,
                            attempt_number,
                            prompt_context_chars=prompt_context_chars,
                        )
                        logger.info(
                            "[AI_SYS] Deployment attempt prompt prepared deployment_id=%s attempt=%s prompt_context_chars=%s",
                            deployment_id,
                            attempt_number,
                            prompt_context_chars,
                        )

                        dockerfile = await asyncio.to_thread(
                            self._generate_dockerfile,
                            prompt_context,
                            attempt_number,
                            max_attempts,
                            technology,
                        )
                        self._update_attempt(
                            deployment_id,
                            attempt_number,
                            dockerfile=dockerfile,
                            status="dockerfile_generated",
                        )
                        await asyncio.to_thread(
                            (repo_dir / "Dockerfile").write_text,
                            dockerfile,
                            "utf-8",
                        )
                        self._assert_not_cancelled(deployment_id)
                        logger.info(
                            "[AI_SYS] Dockerfile generated deployment_id=%s attempt=%s bytes=%s",
                            deployment_id,
                            attempt_number,
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
                        self._update_attempt(
                            deployment_id,
                            attempt_number,
                            status="build_succeeded",
                            finished_at=datetime.utcnow(),
                        )
                        self._assert_not_cancelled(deployment_id)
                        logger.info(
                            "[AI_SYS] Container built and started deployment_id=%s attempt=%s image=%s container_id=%s name=%s port=%s",
                            deployment_id,
                            attempt_number,
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
                            "[AI_SYS] Deployment pipeline completed deployment_id=%s attempt=%s public_url=%s",
                            deployment_id,
                            attempt_number,
                            public_url,
                        )
                        return
                    except BuildFailedError as exc:
                        build_error = str(exc)
                        self._update_attempt(
                            deployment_id,
                            attempt_number,
                            status="build_failed",
                            build_error=build_error,
                            finished_at=datetime.utcnow(),
                        )
                        logger.warning(
                            "[AI_SYS] Deployment attempt failed deployment_id=%s attempt=%s/%s reason=%s",
                            deployment_id,
                            attempt_number,
                            max_attempts,
                            build_error,
                        )
                        if attempt_number >= max_attempts:
                            self._update_deployment(
                                deployment_id,
                                status="failed",
                                error_message=f"Build failed after {attempt_number} attempts: {build_error}",
                            )
                            logger.error(
                                "[AI_SYS] Deployment failed after max attempts deployment_id=%s attempts=%s",
                                deployment_id,
                                max_attempts,
                            )
                            return

                        self._update_deployment(
                            deployment_id,
                            status="retrying",
                            error_message=f"Attempt {attempt_number} failed; retrying",
                        )
                        logger.info(
                            "[AI_SYS] Deployment retry scheduled deployment_id=%s next_attempt=%s",
                            deployment_id,
                            attempt_number + 1,
                        )
                    except DeploymentCancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        self._update_attempt(
                            deployment_id,
                            attempt_number,
                            status="failed",
                            build_error=str(exc),
                            finished_at=datetime.utcnow(),
                        )
                        logger.error(
                            "[AI_SYS] Deployment attempt non-retryable failure deployment_id=%s attempt=%s error=%s",
                            deployment_id,
                            attempt_number,
                            exc,
                        )
                        raise
        except DeploymentCancelledError:
            logger.warning(
                "[AI_SYS] Deployment cancelled deployment_id=%s",
                deployment_id,
            )
            with self._lock:
                cancelled_record = self._deployments.get(deployment_id)
            if cancelled_record:
                cleanup_record = self._clone_deployment_record(cancelled_record)
                await asyncio.to_thread(self._cleanup_resources, cleanup_record)
                self._update_deployment(
                    deployment_id,
                    status="deleted",
                    docker_image=None,
                    container_id=None,
                    container_name=None,
                    container_port=None,
                    public_url=None,
                    error_message=None,
                )
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
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "[AI_SYS] Deployment failed unexpected deployment_id=%s error=%s",
                deployment_id,
                exc,
            )
            self._update_deployment(deployment_id, status="failed", error_message=str(exc))

    def _record_to_status_response(self, record: DeploymentRecord) -> DeploymentStatusResponse:
        attempts = [
            DeploymentAttemptRead(
                attempt=item.attempt,
                status=item.status,
                technology=item.technology,
                dockerfile=item.dockerfile,
                build_error=item.build_error,
                prompt_context_chars=item.prompt_context_chars,
                started_at=item.started_at,
                finished_at=item.finished_at,
            )
            for item in record.attempts
        ]
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
            current_attempt=record.current_attempt,
            max_attempts=record.max_attempts,
            attempts=attempts,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def _clone_attempt_record(self, attempt: DeploymentAttemptRecord) -> DeploymentAttemptRecord:
        return DeploymentAttemptRecord(
            attempt=attempt.attempt,
            status=attempt.status,
            technology=attempt.technology,
            dockerfile=attempt.dockerfile,
            build_error=attempt.build_error,
            prompt_context_chars=attempt.prompt_context_chars,
            started_at=attempt.started_at,
            finished_at=attempt.finished_at,
        )

    def _clone_deployment_record(self, record: DeploymentRecord) -> DeploymentRecord:
        return DeploymentRecord(
            deployment_id=record.deployment_id,
            tenant_id=record.tenant_id,
            github_url=record.github_url,
            status=record.status,
            created_at=record.created_at,
            updated_at=record.updated_at,
            docker_image=record.docker_image,
            container_id=record.container_id,
            container_name=record.container_name,
            container_port=record.container_port,
            public_url=record.public_url,
            error_message=record.error_message,
            current_attempt=record.current_attempt,
            max_attempts=record.max_attempts,
            attempts=[self._clone_attempt_record(item) for item in record.attempts],
            cancel_requested=record.cancel_requested,
        )

    def _build_record_from_db(
        self,
        db_record: DeploymentModel,
        db_attempts: list[DeploymentAttemptModel],
    ) -> DeploymentRecord:
        attempts = [
            DeploymentAttemptRecord(
                attempt=item.attempt,
                status=item.status,
                technology=item.technology,
                dockerfile=item.dockerfile,
                build_error=item.build_error,
                prompt_context_chars=item.prompt_context_chars,
                started_at=item.started_at,
                finished_at=item.finished_at,
            )
            for item in sorted(db_attempts, key=lambda value: value.attempt)
        ]
        return DeploymentRecord(
            deployment_id=db_record.deployment_id,
            tenant_id=db_record.tenant_id,
            github_url=db_record.github_url,
            status=db_record.status,
            created_at=db_record.created_at,
            updated_at=db_record.updated_at,
            docker_image=db_record.docker_image,
            container_id=db_record.container_id,
            container_name=db_record.container_name,
            container_port=db_record.container_port,
            public_url=db_record.public_url,
            error_message=db_record.error_message,
            current_attempt=db_record.current_attempt,
            max_attempts=db_record.max_attempts,
            attempts=attempts,
            cancel_requested=db_record.cancel_requested,
        )

    def _load_deployment_from_db(
        self,
        deployment_id: str,
        tenant_id: int,
    ) -> DeploymentRecord | None:
        with Session(engine) as session:
            db_record = session.exec(
                select(DeploymentModel).where(
                    DeploymentModel.deployment_id == deployment_id,
                    DeploymentModel.tenant_id == tenant_id,
                )
            ).first()
            if not db_record:
                return None
            db_attempts = session.exec(
                select(DeploymentAttemptModel)
                .where(DeploymentAttemptModel.deployment_record_id == db_record.id)
                .order_by(DeploymentAttemptModel.attempt.asc())
            ).all()
            return self._build_record_from_db(db_record, db_attempts)

    def _list_deployments_from_db(
        self,
        tenant_id: int,
        limit: int,
        offset: int,
    ) -> list[DeploymentRecord]:
        with Session(engine) as session:
            db_records = session.exec(
                select(DeploymentModel)
                .where(DeploymentModel.tenant_id == tenant_id)
                .order_by(DeploymentModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            records: list[DeploymentRecord] = []
            for db_record in db_records:
                db_attempts = session.exec(
                    select(DeploymentAttemptModel)
                    .where(DeploymentAttemptModel.deployment_record_id == db_record.id)
                    .order_by(DeploymentAttemptModel.attempt.asc())
                ).all()
                records.append(self._build_record_from_db(db_record, db_attempts))
            return records

    def _get_or_load_record_for_tenant(self, deployment_id: str, tenant_id: int) -> DeploymentRecord | None:
        with self._lock:
            in_memory = self._deployments.get(deployment_id)
            if in_memory and in_memory.tenant_id == tenant_id:
                return self._clone_deployment_record(in_memory)
            if in_memory and in_memory.tenant_id != tenant_id:
                return None

        from_db = self._load_deployment_from_db(deployment_id=deployment_id, tenant_id=tenant_id)
        if not from_db:
            return None

        with self._lock:
            existing = self._deployments.get(deployment_id)
            if not existing:
                self._deployments[deployment_id] = from_db
                return self._clone_deployment_record(from_db)
            if existing.tenant_id != tenant_id:
                return None
            return self._clone_deployment_record(existing)

    def _persist_deployment_state(self, deployment_id: str) -> None:
        with self._lock:
            record = self._deployments.get(deployment_id)
            if not record:
                return
            snapshot = self._clone_deployment_record(record)
        self._save_record_to_db(snapshot)

    def _save_record_to_db(self, record: DeploymentRecord) -> None:
        try:
            with Session(engine) as session:
                db_record = session.exec(
                    select(DeploymentModel).where(DeploymentModel.deployment_id == record.deployment_id)
                ).first()
                if not db_record:
                    db_record = DeploymentModel(
                        deployment_id=record.deployment_id,
                        tenant_id=record.tenant_id,
                        github_url=record.github_url,
                        status=record.status,
                        created_at=record.created_at,
                        updated_at=record.updated_at,
                    )

                db_record.tenant_id = record.tenant_id
                db_record.github_url = record.github_url
                db_record.status = record.status
                db_record.docker_image = record.docker_image
                db_record.container_id = record.container_id
                db_record.container_name = record.container_name
                db_record.container_port = record.container_port
                db_record.public_url = record.public_url
                db_record.error_message = record.error_message
                db_record.current_attempt = record.current_attempt
                db_record.max_attempts = record.max_attempts
                db_record.cancel_requested = record.cancel_requested
                db_record.created_at = record.created_at
                db_record.updated_at = record.updated_at
                session.add(db_record)
                session.commit()
                session.refresh(db_record)

                existing_attempts = session.exec(
                    select(DeploymentAttemptModel).where(
                        DeploymentAttemptModel.deployment_record_id == db_record.id
                    )
                ).all()
                existing_map = {item.attempt: item for item in existing_attempts}
                current_attempt_numbers = {item.attempt for item in record.attempts}

                for attempt in record.attempts:
                    db_attempt = existing_map.get(attempt.attempt)
                    if not db_attempt:
                        db_attempt = DeploymentAttemptModel(
                            deployment_record_id=db_record.id,
                            attempt=attempt.attempt,
                            status=attempt.status,
                            started_at=attempt.started_at,
                        )

                    db_attempt.status = attempt.status
                    db_attempt.technology = attempt.technology
                    db_attempt.dockerfile = attempt.dockerfile
                    db_attempt.build_error = attempt.build_error
                    db_attempt.prompt_context_chars = attempt.prompt_context_chars
                    db_attempt.started_at = attempt.started_at
                    db_attempt.finished_at = attempt.finished_at
                    session.add(db_attempt)

                for attempt_number, db_attempt in existing_map.items():
                    if attempt_number not in current_attempt_numbers:
                        session.delete(db_attempt)

                session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "[AI_SYS] Failed to persist deployment state deployment_id=%s error=%s",
                record.deployment_id,
                exc,
            )

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
        self._persist_deployment_state(deployment_id)

    def _start_attempt(self, deployment_id: str, attempt_number: int, technology: str | None) -> None:
        with self._lock:
            record = self._deployments.get(deployment_id)
            if not record:
                logger.warning(
                    "[AI_SYS] Attempt start skipped; record missing deployment_id=%s attempt=%s",
                    deployment_id,
                    attempt_number,
                )
                return
            attempt = DeploymentAttemptRecord(
                attempt=attempt_number,
                status="running",
                technology=technology,
                dockerfile=None,
                build_error=None,
                prompt_context_chars=0,
                started_at=datetime.utcnow(),
                finished_at=None,
            )
            record.current_attempt = attempt_number
            record.attempts.append(attempt)
            record.updated_at = datetime.utcnow()
        self._persist_deployment_state(deployment_id)

    def _update_attempt(self, deployment_id: str, attempt_number: int, **changes: object) -> None:
        with self._lock:
            record = self._deployments.get(deployment_id)
            if not record:
                logger.warning(
                    "[AI_SYS] Attempt update skipped; record missing deployment_id=%s attempt=%s",
                    deployment_id,
                    attempt_number,
                )
                return

            target: DeploymentAttemptRecord | None = None
            for item in record.attempts:
                if item.attempt == attempt_number:
                    target = item
                    break

            if not target:
                logger.warning(
                    "[AI_SYS] Attempt update skipped; attempt missing deployment_id=%s attempt=%s",
                    deployment_id,
                    attempt_number,
                )
                return

            for key, value in changes.items():
                setattr(target, key, value)
            record.updated_at = datetime.utcnow()
        self._persist_deployment_state(deployment_id)

    def _get_max_attempts(self, deployment_id: str) -> int:
        with self._lock:
            record = self._deployments.get(deployment_id)
            if not record:
                return self._effective_max_attempts()
            return max(1, min(record.max_attempts, 3))

    def _effective_max_attempts(self) -> int:
        return max(1, min(int(self.settings.ai_deploy_max_attempts), 3))

    def _effective_retry_context_max_chars(self) -> int:
        return max(20_000, int(self.settings.ai_deploy_retry_context_max_chars))

    def _attempts_snapshot(self, deployment_id: str) -> list[DeploymentAttemptRecord]:
        with self._lock:
            record = self._deployments.get(deployment_id)
            if not record:
                return []
            return [
                DeploymentAttemptRecord(
                    attempt=item.attempt,
                    status=item.status,
                    technology=item.technology,
                    dockerfile=item.dockerfile,
                    build_error=item.build_error,
                    prompt_context_chars=item.prompt_context_chars,
                    started_at=item.started_at,
                    finished_at=item.finished_at,
                )
                for item in record.attempts
            ]

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

    def _detect_technology(self, repo_dir: Path, context: RepositoryContext) -> str | None:
        score: dict[str, int] = {name: 0 for name in self.TECHNOLOGY_SIGNATURES}
        candidates = self._list_repository_files(repo_dir)
        candidates.extend(context.metadata_files.keys())
        candidates.extend(context.entrypoints.keys())
        seen: set[str] = set()

        for raw_path in candidates:
            relative = raw_path.replace("\\", "/")
            if relative in seen:
                continue
            seen.add(relative)
            name = Path(relative).name
            for technology, signatures in self.TECHNOLOGY_SIGNATURES.items():
                for signature in signatures:
                    lowered_signature = signature.lower()
                    lowered_name = name.lower()
                    lowered_relative = relative.lower()
                    if lowered_signature.startswith("."):
                        if lowered_name.endswith(lowered_signature):
                            score[technology] += 1
                    elif lowered_name == lowered_signature or lowered_relative.endswith(f"/{lowered_signature}"):
                        score[technology] += 1

        sorted_score = sorted(score.items(), key=lambda item: item[1], reverse=True)
        if not sorted_score or sorted_score[0][1] <= 0:
            logger.info("[AI_SYS] Technology detection result=unknown")
            return None

        technology = sorted_score[0][0]
        logger.info(
            "[AI_SYS] Technology detection result=%s top_score=%s raw=%s",
            technology,
            sorted_score[0][1],
            score,
        )
        return technology

    def _list_repository_files(self, repo_dir: Path) -> list[str]:
        files: list[str] = []
        for root, dirs, filenames in os.walk(repo_dir):
            dirs[:] = sorted(directory for directory in dirs if directory not in self.EXCLUDED_DIRS)
            root_path = Path(root)
            for filename in sorted(filenames):
                path = root_path / filename
                files.append(path.relative_to(repo_dir).as_posix())
        return files

    def _collect_enriched_files(self, repo_dir: Path, technology: str | None) -> dict[str, str]:
        patterns: set[str] = set()
        if technology and technology in self.TECHNOLOGY_BUILD_FILES:
            patterns.update(self.TECHNOLOGY_BUILD_FILES[technology])
        else:
            for group in self.TECHNOLOGY_BUILD_FILES.values():
                patterns.update(group)

        selected: dict[str, str] = {}
        for relative in self._list_repository_files(repo_dir):
            name = Path(relative).name
            if name in self.COMMON_BUILD_FILES:
                selected[relative] = self._read_limited(repo_dir / relative)
                continue

            if any(fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(relative, pattern) for pattern in patterns):
                selected[relative] = self._read_limited(repo_dir / relative)

        logger.info(
            "[AI_SYS] Enriched files collected technology=%s files=%s",
            technology or "unknown",
            len(selected),
        )
        return selected

    def _build_prompt_context(
        self,
        repo_dir: Path,
        context: RepositoryContext,
        technology: str | None,
        attempt_number: int,
        deployment_id: str,
    ) -> tuple[dict[str, object], int]:
        payload: dict[str, object] = {
            "attempt_number": attempt_number,
            "detected_technology": technology,
            "directory_tree": context.directory_tree,
            "metadata_files": context.metadata_files,
            "entrypoint_files": context.entrypoints,
        }

        if attempt_number > 1:
            attempt_history: list[dict[str, object | None]] = []
            for item in self._attempts_snapshot(deployment_id):
                if item.attempt >= attempt_number:
                    continue
                attempt_history.append(
                    {
                        "attempt": item.attempt,
                        "status": item.status,
                        "technology": item.technology,
                        "dockerfile": item.dockerfile,
                        "build_error": item.build_error,
                        "prompt_context_chars": item.prompt_context_chars,
                        "started_at": item.started_at.isoformat(),
                        "finished_at": item.finished_at.isoformat() if item.finished_at else None,
                    }
                )
            payload["retry_feedback"] = attempt_history
            payload["enriched_files"] = self._collect_enriched_files(repo_dir, technology)

        max_chars = self._effective_retry_context_max_chars()
        payload = self._shrink_prompt_context(payload, max_chars)
        context_chars = self._prompt_context_size(payload)
        return payload, context_chars

    def _prompt_context_size(self, payload: dict[str, object]) -> int:
        return len(json.dumps(payload, ensure_ascii=True, indent=2))

    def _shrink_prompt_context(self, payload: dict[str, object], max_chars: int) -> dict[str, object]:
        current = self._prompt_context_size(payload)
        if current <= max_chars:
            return payload

        logger.info(
            "[AI_SYS] Prompt context exceeds limit initial_chars=%s max_chars=%s; shrinking",
            current,
            max_chars,
        )

        for _ in range(500):
            current = self._prompt_context_size(payload)
            if current <= max_chars:
                break

            enriched = payload.get("enriched_files")
            if self._shrink_largest_map_entry(enriched):
                continue

            feedback = payload.get("retry_feedback")
            if self._shrink_retry_feedback(feedback):
                continue

            entrypoints = payload.get("entrypoint_files")
            if self._shrink_largest_map_entry(entrypoints):
                continue

            metadata = payload.get("metadata_files")
            if self._shrink_largest_map_entry(metadata):
                continue

            directory_tree = payload.get("directory_tree")
            if isinstance(directory_tree, str) and len(directory_tree) > 2_000:
                payload["directory_tree"] = self._truncate_text(directory_tree, int(len(directory_tree) * 0.75))
                continue

            break

        final_size = self._prompt_context_size(payload)
        if final_size > max_chars:
            logger.warning(
                "[AI_SYS] Prompt context still above limit final_chars=%s max_chars=%s",
                final_size,
                max_chars,
            )
        else:
            logger.info(
                "[AI_SYS] Prompt context shrink complete final_chars=%s max_chars=%s",
                final_size,
                max_chars,
            )
        return payload

    def _shrink_largest_map_entry(self, maybe_map: object) -> bool:
        if not isinstance(maybe_map, dict) or not maybe_map:
            return False

        largest_key: object | None = None
        largest_value_len = -1
        for key, value in maybe_map.items():
            if isinstance(value, str) and len(value) > largest_value_len:
                largest_key = key
                largest_value_len = len(value)

        if not largest_key:
            return False

        current_value = maybe_map[largest_key]
        if not isinstance(current_value, str):
            maybe_map.pop(largest_key, None)
            return True
        if len(current_value) <= 512:
            maybe_map.pop(largest_key, None)
            return True

        maybe_map[largest_key] = self._truncate_text(current_value, int(len(current_value) * 0.6))
        return True

    def _shrink_retry_feedback(self, maybe_feedback: object) -> bool:
        if not isinstance(maybe_feedback, list) or not maybe_feedback:
            return False

        for item in reversed(maybe_feedback):
            if not isinstance(item, dict):
                continue
            for key in ("build_error", "dockerfile"):
                value = item.get(key)
                if isinstance(value, str) and len(value) > 1_500:
                    item[key] = self._truncate_text(value, int(len(value) * 0.6))
                    return True

        if len(maybe_feedback) > 1:
            maybe_feedback.pop(0)
            return True
        return False

    def _truncate_text(self, value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return f"{value[:limit]}\n... (truncated for retry context)"

    def _generate_dockerfile(
        self,
        prompt_context: dict[str, object],
        attempt_number: int,
        max_attempts: int,
        technology: str | None,
    ) -> str:
        if not self.settings.PROXYAPI_API_KEY:
            logger.error("[AI_SYS] PROXYAPI_API_KEY is missing")
            raise RuntimeError("PROXYAPI_API_KEY is not configured")

        logger.info(
            "[AI_SYS] ProxyAPI/OpenRouter Dockerfile generation started attempt=%s/%s technology=%s",
            attempt_number,
            max_attempts,
            technology or "unknown",
        )

        retry_instructions = ""
        if attempt_number > 1:
            retry_instructions = (
                "Retry mode instructions:\n"
                "- The previous build attempt failed.\n"
                "- Analyze retry_feedback and fix the root cause in the Dockerfile.\n"
                "- Prioritize valid dependency restore/build paths and startup command.\n\n"
            )

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
            "- Expose a typical app port when identifiable.\n"
            f"- This is attempt {attempt_number} of {max_attempts}.\n"
            f"- Detected technology: {technology or 'unknown'}.\n\n"
            f"{retry_instructions}"
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
            resolved_container_port = self._resolve_runtime_container_port(
                container=container,
                container_name=container_name,
                fallback_port=container_port,
                deployment_id=deployment_id,
            )
            logger.info(
                "[AI_SYS] Container started deployment_id=%s container_id=%s container_name=%s container_port=%s",
                deployment_id,
                container.id,
                container_name,
                resolved_container_port,
            )
            return image_tag, container.id, container_name, resolved_container_port
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

    def _resolve_runtime_container_port(
        self,
        container: object,
        container_name: str,
        fallback_port: int,
        deployment_id: str,
    ) -> int:
        candidates = self._collect_container_port_candidates(container, fallback_port)
        logger.info(
            "[AI_SYS] Runtime port candidates prepared deployment_id=%s container_name=%s candidates=%s",
            deployment_id,
            container_name,
            candidates,
        )

        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                container.reload()
            except Exception:  # noqa: BLE001
                logger.debug(
                    "[AI_SYS] Container reload failed during port detection deployment_id=%s",
                    deployment_id,
                    exc_info=True,
                )

            container_status = str(getattr(container, "status", "")).lower()
            if container_status in {"exited", "dead"}:
                logs = self._read_container_logs(container)
                raise RuntimeError(
                    f"Container exited before app port became reachable. status={container_status}. logs={logs}"
                )

            for port in candidates:
                if self._is_tcp_port_open(container_name, port, timeout_sec=0.8):
                    logger.info(
                        "[AI_SYS] Runtime port detected deployment_id=%s container_name=%s port=%s",
                        deployment_id,
                        container_name,
                        port,
                    )
                    return port

            time.sleep(1)

        logs = self._read_container_logs(container)
        raise RuntimeError(
            "Container app port is not reachable on network "
            f"(candidates={candidates}). Last logs: {logs}"
        )

    def _collect_container_port_candidates(self, container: object, fallback_port: int) -> list[int]:
        candidates: list[int] = []

        def _add_port(value: object) -> None:
            try:
                port = int(value)
            except Exception:  # noqa: BLE001
                return
            if 1 <= port <= 65535 and port not in candidates:
                candidates.append(port)

        _add_port(fallback_port)

        attrs = getattr(container, "attrs", {}) or {}
        exposed_ports = attrs.get("Config", {}).get("ExposedPorts") or {}
        if isinstance(exposed_ports, dict):
            for key in sorted(exposed_ports):
                match = re.match(r"^(\d+)/(tcp|udp)$", str(key))
                if match:
                    _add_port(match.group(1))

        env_list = attrs.get("Config", {}).get("Env") or []
        if isinstance(env_list, list):
            for item in env_list:
                if not isinstance(item, str) or "=" not in item:
                    continue
                env_key, env_value = item.split("=", 1)
                key = env_key.strip().upper()
                value = env_value.strip()
                if key in {"PORT", "APP_PORT", "SERVER_PORT", "HTTP_PORT"}:
                    _add_port(value)
                if key in {"ASPNETCORE_URLS", "URLS"}:
                    for match in re.finditer(r":(\d{2,5})", value):
                        _add_port(match.group(1))

        for common_port in (80, 443, 3000, 3001, 5000, 5001, 8000, 8080, 8081, 8888, 9000):
            _add_port(common_port)

        return candidates

    def _is_tcp_port_open(self, host: str, port: int, timeout_sec: float) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout_sec):
                return True
        except OSError:
            return False

    def _read_container_logs(self, container: object) -> str:
        try:
            raw_logs = container.logs(tail=60)
            if isinstance(raw_logs, bytes):
                text = raw_logs.decode("utf-8", errors="replace")
            else:
                text = str(raw_logs)
            return text.strip()[-4000:]
        except Exception:  # noqa: BLE001
            return "logs unavailable"

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

    def _deployment_host_domain(self) -> str:
        return self.settings.deployment_host_domain.strip() or self.settings.domain.strip()

    def _configure_public_access(self, deployment_id: str, container_name: str, container_port: int) -> str | None:
        deployment_domain = self._deployment_host_domain()
        if not deployment_domain:
            logger.warning(
                "[AI_SYS] Public access skipped because DEPLOYMENT_HOST_DOMAIN/DOMAIN is not configured deployment_id=%s",
                deployment_id,
            )
            return None

        scheme = self.settings.deployment_public_scheme.strip().lower() or "http"
        public_host = f"{deployment_id}.{deployment_domain}"
        tls_cert_path, tls_key_path = self._resolve_deployment_tls_paths(scheme)
        server_block = self._render_nginx_server_block(
            server_name=public_host,
            container_name=container_name,
            container_port=container_port,
            scheme=scheme,
            tls_cert_path=tls_cert_path,
            tls_key_path=tls_key_path,
        )
        logger.info(
            "[AI_SYS] Configuring public access deployment_id=%s public_host=%s scheme=%s upstream=%s:%s",
            deployment_id,
            public_host,
            scheme,
            container_name,
            container_port,
        )
        if scheme == "https":
            logger.warning(
                "[AI_SYS] HTTPS subdomain routing requires wildcard DNS and wildcard certificate public_host=%s cert=%s key=%s",
                public_host,
                tls_cert_path,
                tls_key_path,
            )
        else:
            logger.warning(
                "[AI_SYS] Subdomain routing requires wildcard DNS (*.domain) to point to nginx host public_host=%s",
                public_host,
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
            self._put_text_file(nginx_container, self.NGINX_DYNAMIC_DIR, config_name, server_block)
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

        logger.info(
            "[AI_SYS] Public access configured deployment_id=%s url=%s://%s/",
            deployment_id,
            scheme,
            public_host,
        )
        return f"{scheme}://{public_host}/"

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
        if not self._deployment_host_domain():
            logger.info(
                "[AI_SYS] Public access removal skipped because DEPLOYMENT_HOST_DOMAIN/DOMAIN is not configured deployment_id=%s",
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
            nginx_container.exec_run(["rm", "-f", f"{self.LEGACY_NGINX_DYNAMIC_DIR}/{deployment_id}.conf"])
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

    def _resolve_deployment_tls_paths(self, scheme: str) -> tuple[str | None, str | None]:
        if scheme != "https":
            return None, None

        configured_cert = self.settings.deployment_tls_cert_path.strip()
        configured_key = self.settings.deployment_tls_key_path.strip()
        if configured_cert and configured_key:
            return configured_cert, configured_key
        if configured_cert or configured_key:
            raise RuntimeError("Both DEPLOYMENT_TLS_CERT_PATH and DEPLOYMENT_TLS_KEY_PATH must be set for HTTPS")

        domain = self._deployment_host_domain()
        if not domain:
            raise RuntimeError("DEPLOYMENT_HOST_DOMAIN or DOMAIN is required for HTTPS hosted deployments")

        return (
            f"/etc/letsencrypt/live/{domain}/fullchain.pem",
            f"/etc/letsencrypt/live/{domain}/privkey.pem",
        )

    def _render_nginx_server_block(
        self,
        server_name: str,
        container_name: str,
        container_port: int,
        scheme: str,
        tls_cert_path: str | None,
        tls_key_path: str | None,
    ) -> str:
        proxy_location = (
            "    location / {\n"
            f"        proxy_pass http://{container_name}:{container_port};\n"
            "        proxy_http_version 1.1;\n"
            "        proxy_set_header Host $host;\n"
            "        proxy_set_header X-Real-IP $remote_addr;\n"
            "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
            "        proxy_set_header X-Forwarded-Proto $scheme;\n"
            "        proxy_set_header Upgrade $http_upgrade;\n"
            "        proxy_set_header Connection \"upgrade\";\n"
            "    }\n"
        )
        if scheme == "https":
            if not tls_cert_path or not tls_key_path:
                raise RuntimeError("TLS certificate paths are required for HTTPS hosted deployments")
            return (
                "server {\n"
                "    listen 80;\n"
                f"    server_name {server_name};\n"
                "    return 301 https://$host$request_uri;\n"
                "}\n\n"
                "server {\n"
                "    listen 443 ssl;\n"
                "    http2 on;\n"
                f"    server_name {server_name};\n"
                f"    ssl_certificate {tls_cert_path};\n"
                f"    ssl_certificate_key {tls_key_path};\n"
                "    ssl_protocols TLSv1.2 TLSv1.3;\n"
                "    ssl_prefer_server_ciphers on;\n"
                f"{proxy_location}"
                "}\n"
            )
        return (
            "server {\n"
            "    listen 80;\n"
            f"    server_name {server_name};\n\n"
            f"{proxy_location}"
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
