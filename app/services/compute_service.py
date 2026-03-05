import logging
import secrets
from datetime import datetime

from fastapi import BackgroundTasks, HTTPException, status
from sqlmodel import Session, select

from app.core.config import get_settings
from app.db.session import engine
from app.models import (
    ActionType,
    Flavor,
    Image,
    Instance,
    InstanceOperation,
    InstanceOperationStatus,
    InstanceOperationType,
    InstanceStatus,
    Task,
    TaskRun,
    TaskRunStatus,
)
from app.providers.compute.docker_provider import get_docker_provider
from app.services.billing_service import BillingService

logger = logging.getLogger(__name__)


class ComputeService:
    def __init__(self) -> None:
        self.billing_service = BillingService()

    def _allocate_ssh_port(self, session: Session) -> int:
        settings = get_settings()
        start = settings.ssh_port_range_start
        end = settings.ssh_port_range_end
        if start > end:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="SSH port range is invalid",
            )
        used_ports = set(
            session.exec(
                select(Instance.ssh_port).where(
                    Instance.status != InstanceStatus.TERMINATED
                ),
            ).all()
        )
        for port in range(start, end + 1):
            if port not in used_ports:
                return port
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No SSH ports available",
        )

    def _build_ssh_credentials(self, tenant_id: int) -> tuple[str, str]:
        settings = get_settings()
        username = f"{settings.ssh_username_prefix}{tenant_id}"
        password = secrets.token_urlsafe(16)
        return username, password

    def _build_postgres_credentials(self, tenant_id: int) -> tuple[str, str]:
        settings = get_settings()
        username = f"{settings.ssh_username_prefix}{tenant_id}"
        password = secrets.token_urlsafe(16)
        return username, password

    def ensure_docker_available(self) -> None:
        provider = get_docker_provider()
        provider.ping()

    def list_instances(self, session: Session, tenant_id: int) -> list[Instance]:
        return session.exec(
            select(Instance)
            .where(Instance.tenant_id == tenant_id)
            .order_by(Instance.created_at.desc()),
        ).all()

    def get_instance(
        self, session: Session, tenant_id: int, instance_id: int
    ) -> Instance:
        instance = session.exec(
            select(Instance).where(
                Instance.id == instance_id, Instance.tenant_id == tenant_id
            ),
        ).first()
        if not instance:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Instance not found"
            )
        return instance

    def request_instance_creation(
        self,
        session: Session,
        background_tasks: BackgroundTasks,
        tenant_id: int,
        name: str,
        flavor_id: int,
        image_id: int,
    ) -> tuple[Instance, InstanceOperation, str, str | None]:
        flavor = session.get(Flavor, flavor_id)
        if not flavor:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Flavor not found"
            )

        image = session.get(Image, image_id)
        if not image or not image.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Image not found"
            )

        existing = session.exec(
            select(Instance).where(
                Instance.tenant_id == tenant_id, Instance.name == name
            ),
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Instance name already exists",
            )

        self.billing_service.assert_can_allocate(session, tenant_id, flavor)
        settings = get_settings()
        ssh_port = self._allocate_ssh_port(session)
        ssh_username, ssh_password = self._build_ssh_credentials(tenant_id)
        postgres_username = None
        postgres_password = None
        if image.code == settings.postgres_image_code:
            postgres_username, postgres_password = self._build_postgres_credentials(
                tenant_id
            )

        instance = Instance(
            tenant_id=tenant_id,
            name=name,
            flavor_id=flavor_id,
            image_id=image_id,
            ssh_port=ssh_port,
            ssh_username=ssh_username,
            postgres_username=postgres_username,
            status=InstanceStatus.PROVISIONING,
            updated_at=datetime.utcnow(),
        )
        session.add(instance)
        session.flush()

        operation = InstanceOperation(
            tenant_id=tenant_id,
            instance_id=instance.id,
            type=InstanceOperationType.CREATE,
            status=InstanceOperationStatus.PENDING,
        )
        session.add(operation)
        session.commit()
        session.refresh(instance)
        session.refresh(operation)

        background_tasks.add_task(
            self._provision_instance_task,
            instance.id,
            operation.id,
            ssh_password,
            postgres_password,
        )
        return instance, operation, ssh_password, postgres_password

    def _provision_instance_task(
        self,
        instance_id: int,
        operation_id: int,
        ssh_password: str,
        postgres_password: str | None,
    ) -> None:
        provider = get_docker_provider()
        with Session(engine) as session:
            operation = session.get(InstanceOperation, operation_id)
            instance = session.get(Instance, instance_id)
            if not operation or not instance:
                return

            operation.status = InstanceOperationStatus.RUNNING
            session.add(operation)
            session.commit()

            container_id: str | None = None
            try:
                flavor = session.get(Flavor, instance.flavor_id)
                image = session.get(Image, instance.image_id)
                if not flavor or not image:
                    raise RuntimeError("Flavor or image not found for provisioning")

                settings = get_settings()
                environment = {
                    "SSH_USER": instance.ssh_username,
                    "SSH_PASSWORD": ssh_password,
                }
                privileged = False
                if image.code == settings.postgres_image_code:
                    if not postgres_password or not instance.postgres_username:
                        raise RuntimeError("Postgres credentials are missing")
                    environment["POSTGRES_USER"] = instance.postgres_username
                    environment["POSTGRES_PASSWORD"] = postgres_password
                if image.code == settings.docker_image_code:
                    environment["DOCKER_TLS_CERTDIR"] = ""
                    privileged = True

                container_id = provider.create_instance(
                    base_name=instance.name,
                    image_ref=image.docker_image_ref,
                    cpu=flavor.cpu,
                    ram_mb=flavor.ram_mb,
                    environment=environment,
                    ports={"22/tcp": instance.ssh_port},
                    privileged=privileged,
                )
                provider.start_instance(container_id)
                ip_address = provider.get_instance_ip(container_id)

                instance.status = InstanceStatus.RUNNING
                instance.docker_container_id = container_id
                instance.ip_address = ip_address
                instance.updated_at = datetime.utcnow()

                operation.status = InstanceOperationStatus.SUCCESS
                operation.finished_at = datetime.utcnow()

                session.add(instance)
                session.add(operation)
                session.commit()
            except Exception as exc:  # noqa: BLE001 - store operation failure in DB
                if container_id:
                    try:
                        provider.stop_instance(container_id)
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "Failed to stop container after provision error instance_id=%s container_id=%s",
                            instance_id,
                            container_id,
                            exc_info=True,
                        )
                    try:
                        provider.remove_instance(container_id)
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "Failed to remove container after provision error instance_id=%s container_id=%s",
                            instance_id,
                            container_id,
                            exc_info=True,
                        )
                instance.status = InstanceStatus.ERROR
                instance.updated_at = datetime.utcnow()
                operation.status = InstanceOperationStatus.FAILED
                operation.error_message = str(exc)
                operation.finished_at = datetime.utcnow()
                session.add(instance)
                session.add(operation)
                session.commit()

    def get_operation(
        self, session: Session, tenant_id: int, operation_id: int
    ) -> InstanceOperation:
        operation = session.exec(
            select(InstanceOperation).where(
                InstanceOperation.id == operation_id,
                InstanceOperation.tenant_id == tenant_id,
            ),
        ).first()
        if not operation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found"
            )
        return operation

    def reset_ssh_password(
        self, session: Session, tenant_id: int, instance_id: int
    ) -> tuple[Instance, str]:
        instance = self.get_instance(session, tenant_id, instance_id)
        if instance.status != InstanceStatus.RUNNING or not instance.docker_container_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Instance is not RUNNING",
            )
        password = secrets.token_urlsafe(16)
        command = f"echo '{instance.ssh_username}:{password}' | chpasswd"
        exit_code, _, stderr = get_docker_provider().exec_script(
            instance.docker_container_id,
            command,
        )
        if exit_code != 0:
            logger.warning(
                "Failed to reset SSH password instance_id=%s stderr=%s",
                instance.id,
                stderr,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to reset SSH password",
            )
        return instance, password

    def delete_instance(
        self, session: Session, tenant_id: int, instance_id: int
    ) -> None:
        instance = self.get_instance(session, tenant_id, instance_id)
        if instance.status == InstanceStatus.TERMINATED:
            return

        running_task = session.exec(
            select(TaskRun)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                TaskRun.instance_id == instance.id,
                TaskRun.status == TaskRunStatus.RUNNING,
                Task.tenant_id == tenant_id,
            ),
        ).first()
        if running_task:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Instance has running tasks and cannot be deleted",
            )

        operation = InstanceOperation(
            tenant_id=tenant_id,
            instance_id=instance.id,
            type=InstanceOperationType.DELETE,
            status=InstanceOperationStatus.RUNNING,
        )
        session.add(operation)
        session.flush()

        provider = get_docker_provider()
        if instance.docker_container_id:
            try:
                provider.stop_instance(instance.docker_container_id)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Failed to stop container during delete instance_id=%s container_id=%s",
                    instance.id,
                    instance.docker_container_id,
                    exc_info=True,
                )
            try:
                provider.remove_instance(instance.docker_container_id)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Failed to remove container during delete instance_id=%s container_id=%s",
                    instance.id,
                    instance.docker_container_id,
                    exc_info=True,
                )

        instance.status = InstanceStatus.TERMINATED
        instance.deleted_at = datetime.utcnow()
        instance.updated_at = datetime.utcnow()
        operation.status = InstanceOperationStatus.SUCCESS
        operation.finished_at = datetime.utcnow()

        session.add(instance)
        session.add(operation)
        session.commit()

    def apply_action(
        self, session: Session, tenant_id: int, instance_id: int, action: ActionType
    ) -> Instance:
        instance = self.get_instance(session, tenant_id, instance_id)
        if instance.status == InstanceStatus.TERMINATED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Instance is terminated"
            )

        provider = get_docker_provider()

        if action == ActionType.START:
            if instance.status == InstanceStatus.RUNNING:
                return instance
            if not instance.docker_container_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Container ID is missing",
                )
            provider.start_instance(instance.docker_container_id)
            instance.status = InstanceStatus.RUNNING
            instance.updated_at = datetime.utcnow()
            session.add(instance)
            session.commit()
            session.refresh(instance)
            return instance

        if action == ActionType.STOP:
            if instance.status == InstanceStatus.STOPPED:
                return instance
            if instance.status != InstanceStatus.RUNNING:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Only RUNNING instance can be stopped",
                )
            if not instance.docker_container_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Container ID is missing",
                )
            provider.stop_instance(instance.docker_container_id)
            instance.status = InstanceStatus.STOPPED
            instance.updated_at = datetime.utcnow()
            session.add(instance)
            session.commit()
            session.refresh(instance)
            return instance

        if action == ActionType.REBOOT:
            if instance.status != InstanceStatus.RUNNING:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Only RUNNING instance can be rebooted",
                )
            if not instance.docker_container_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Container ID is missing",
                )
            provider.reboot_instance(instance.docker_container_id)
            instance.updated_at = datetime.utcnow()
            session.add(instance)
            session.commit()
            session.refresh(instance)
            return instance

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported action"
        )
