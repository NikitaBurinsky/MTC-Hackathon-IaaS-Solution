from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from sqlmodel import Session

from app.core.config import get_settings
from app.core.deps import get_current_tenant_id
from app.db.session import get_session
from app.schemas import (
    InstanceActionRequest,
    InstanceCreateAccepted,
    InstanceCreateRequest,
    InstanceOperationRead,
    InstanceRead,
    InstanceSshInfo,
    InstanceSshResetResponse,
)
from app.services import ComputeService

router = APIRouter(prefix="/instances", tags=["instances"])
compute_service = ComputeService()


def _build_instance_read(instance) -> InstanceRead:
    settings = get_settings()
    return InstanceRead(
        id=instance.id,
        tenant_id=instance.tenant_id,
        name=instance.name,
        flavor_id=instance.flavor_id,
        image_id=instance.image_id,
        status=instance.status,
        ip_address=instance.ip_address,
        ssh_host=settings.ssh_default_host,
        ssh_port=instance.ssh_port,
        ssh_username=instance.ssh_username,
        postgres_username=instance.postgres_username,
        created_at=instance.created_at,
        updated_at=instance.updated_at,
        deleted_at=instance.deleted_at,
    )


@router.get("", response_model=list[InstanceRead])
def list_instances(
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    instances = compute_service.list_instances(session, tenant_id)
    return [_build_instance_read(instance) for instance in instances]


@router.post(
    "", response_model=InstanceCreateAccepted, status_code=status.HTTP_202_ACCEPTED
)
def create_instance(
    payload: InstanceCreateRequest,
    background_tasks: BackgroundTasks,
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    instance, operation, ssh_password, postgres_password = (
        compute_service.request_instance_creation(
            session=session,
            background_tasks=background_tasks,
            tenant_id=tenant_id,
            name=payload.name,
            flavor_id=payload.flavor_id,
            image_id=payload.image_id,
        )
    )
    settings = get_settings()
    return InstanceCreateAccepted(
        instance_id=instance.id,
        provisioning_operation_id=operation.id,
        status=instance.status,
        ssh_host=settings.ssh_default_host,
        ssh_port=instance.ssh_port,
        ssh_username=instance.ssh_username,
        ssh_password=ssh_password,
        postgres_username=instance.postgres_username,
        postgres_password=postgres_password,
    )


@router.get("/operations/{operation_id}", response_model=InstanceOperationRead)
def get_operation(
    operation_id: int,
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    return compute_service.get_operation(session, tenant_id, operation_id)


@router.get("/{instance_id}", response_model=InstanceRead)
def get_instance(
    instance_id: int,
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    instance = compute_service.get_instance(session, tenant_id, instance_id)
    return _build_instance_read(instance)


@router.get("/{instance_id}/ssh", response_model=InstanceSshInfo)
def get_instance_ssh(
    instance_id: int,
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    instance = compute_service.get_instance(session, tenant_id, instance_id)
    settings = get_settings()
    return InstanceSshInfo(
        ssh_host=settings.ssh_default_host,
        ssh_port=instance.ssh_port,
        ssh_username=instance.ssh_username,
    )


@router.post("/{instance_id}/ssh/reset", response_model=InstanceSshResetResponse)
def reset_instance_ssh(
    instance_id: int,
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    instance, password = compute_service.reset_ssh_password(
        session, tenant_id, instance_id
    )
    settings = get_settings()
    return InstanceSshResetResponse(
        ssh_host=settings.ssh_default_host,
        ssh_port=instance.ssh_port,
        ssh_username=instance.ssh_username,
        ssh_password=password,
    )


@router.delete(
    "/{instance_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
def delete_instance(
    instance_id: int,
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    compute_service.delete_instance(session, tenant_id, instance_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{instance_id}/action", response_model=InstanceRead)
def instance_action(
    instance_id: int,
    payload: InstanceActionRequest,
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    instance = compute_service.apply_action(session, tenant_id, instance_id, payload.action)
    return _build_instance_read(instance)
