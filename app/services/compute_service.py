from datetime import datetime

from fastapi import BackgroundTasks, HTTPException, status
from sqlmodel import Session, select

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


class ComputeService:
    def __init__(self) -> None:
        self.billing_service = BillingService()

    def ensure_docker_available(self) -> None:
        provider = get_docker_provider()
        provider.ping()

    def list_instances(self, session: Session, tenant_id: int) -> list[Instance]:
        return session.exec(
            select(Instance).where(Instance.tenant_id == tenant_id).order_by(Instance.created_at.desc()),
        ).all()

    def get_instance(self, session: Session, tenant_id: int, instance_id: int) -> Instance:
        instance = session.exec(
            select(Instance).where(Instance.id == instance_id, Instance.tenant_id == tenant_id),
        ).first()
        if not instance:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instance not found")
        return instance

    def request_instance_creation(
        self,
        session: Session,
        background_tasks: BackgroundTasks,
        tenant_id: int,
        name: str,
        flavor_id: int,
        image_id: int,
    ) -> tuple[Instance, InstanceOperation]:
        flavor = session.get(Flavor, flavor_id)
        if not flavor:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flavor not found")

        image = session.get(Image, image_id)
        if not image or not image.is_active:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")

        self.billing_service.assert_can_allocate(session, tenant_id, flavor)

        instance = Instance(
            tenant_id=tenant_id,
            name=name,
            flavor_id=flavor_id,
            image_id=image_id,
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

        background_tasks.add_task(self._provision_instance_task, instance.id, operation.id)
        return instance, operation

    def _provision_instance_task(self, instance_id: int, operation_id: int) -> None:
        provider = get_docker_provider()
        with Session(engine) as session:
            operation = session.get(InstanceOperation, operation_id)
            instance = session.get(Instance, instance_id)
            if not operation or not instance:
                return

            operation.status = InstanceOperationStatus.RUNNING
            session.add(operation)
            session.commit()

            try:
                flavor = session.get(Flavor, instance.flavor_id)
                image = session.get(Image, instance.image_id)
                if not flavor or not image:
                    raise RuntimeError("Flavor or image not found for provisioning")

                container_id = provider.create_instance(
                    base_name=instance.name,
                    image_ref=image.docker_image_ref,
                    cpu=flavor.cpu,
                    ram_mb=flavor.ram_mb,
                )
                provider.start_instance(container_id)
                ip_address = provider.get_instance_ip(container_id)

                instance.status = InstanceStatus.RUNNING
                instance.docker_container_id = container_id
                instance.ip_address = ip_address
                instance.updated_at = datetime.utcnow()

                operation.status = InstanceOperationStatus.SUCCESS
                operation.finished_at = datetime.utcnow()

                self.billing_service.open_usage_interval(session, instance)
                session.add(instance)
                session.add(operation)
                session.commit()
            except Exception as exc:  # noqa: BLE001 - store operation failure in DB
                instance.status = InstanceStatus.ERROR
                instance.updated_at = datetime.utcnow()
                operation.status = InstanceOperationStatus.FAILED
                operation.error_message = str(exc)
                operation.finished_at = datetime.utcnow()
                session.add(instance)
                session.add(operation)
                session.commit()

    def get_operation(self, session: Session, tenant_id: int, operation_id: int) -> InstanceOperation:
        operation = session.exec(
            select(InstanceOperation).where(
                InstanceOperation.id == operation_id,
                InstanceOperation.tenant_id == tenant_id,
            ),
        ).first()
        if not operation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
        return operation

    def delete_instance(self, session: Session, tenant_id: int, instance_id: int) -> None:
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
                pass
            try:
                provider.remove_instance(instance.docker_container_id)
            except Exception:  # noqa: BLE001
                pass

        if instance.status == InstanceStatus.RUNNING:
            self.billing_service.close_open_usage_interval(session, instance)

        instance.status = InstanceStatus.TERMINATED
        instance.deleted_at = datetime.utcnow()
        instance.updated_at = datetime.utcnow()
        operation.status = InstanceOperationStatus.SUCCESS
        operation.finished_at = datetime.utcnow()

        session.add(instance)
        session.add(operation)
        session.commit()

    def apply_action(self, session: Session, tenant_id: int, instance_id: int, action: ActionType) -> Instance:
        instance = self.get_instance(session, tenant_id, instance_id)
        if instance.status == InstanceStatus.TERMINATED:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Instance is terminated")

        provider = get_docker_provider()

        if action == ActionType.START:
            if instance.status == InstanceStatus.RUNNING:
                return instance
            if not instance.docker_container_id:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Container ID is missing")
            provider.start_instance(instance.docker_container_id)
            instance.status = InstanceStatus.RUNNING
            instance.updated_at = datetime.utcnow()
            self.billing_service.open_usage_interval(session, instance)
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
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Container ID is missing")
            provider.stop_instance(instance.docker_container_id)
            self.billing_service.close_open_usage_interval(session, instance)
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
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Container ID is missing")
            provider.reboot_instance(instance.docker_container_id)
            instance.updated_at = datetime.utcnow()
            session.add(instance)
            session.commit()
            session.refresh(instance)
            return instance

        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported action")

