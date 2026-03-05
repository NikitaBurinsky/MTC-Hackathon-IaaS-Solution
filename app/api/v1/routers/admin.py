from fastapi import APIRouter, Depends, Query, status
from sqlmodel import Session

from app.core.config import get_settings
from app.core.deps import require_admin_or_superuser, require_superuser
from app.db.session import get_session
from app.models import InstanceStatus, User, UserRole
from app.schemas import (
    AdminOverviewResponse,
    AdminTenantRead,
    AdminUsageTenantRead,
    AdminUserRead,
    DeploymentStatusResponse,
    InstanceActionRequest,
    InstanceRead,
    MessageResponse,
)
from app.services import AdminService

router = APIRouter(prefix="/admin", tags=["admin"])
admin_service = AdminService()


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


@router.get("/overview", response_model=AdminOverviewResponse)
def get_admin_overview(
    _: User = Depends(require_admin_or_superuser),
    session: Session = Depends(get_session),
):
    return admin_service.get_overview(session)


@router.get("/tenants", response_model=list[AdminTenantRead])
def list_tenants(
    _: User = Depends(require_admin_or_superuser),
    session: Session = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return admin_service.list_tenants(session=session, limit=limit, offset=offset)


@router.get("/users", response_model=list[AdminUserRead])
def list_users(
    _: User = Depends(require_admin_or_superuser),
    session: Session = Depends(get_session),
    tenant_id: int | None = Query(default=None, ge=1),
    role: UserRole | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return admin_service.list_users(
        session=session,
        tenant_id=tenant_id,
        role=role,
        limit=limit,
        offset=offset,
    )


@router.post("/users/{user_id}/promote", response_model=AdminUserRead)
def promote_user(
    user_id: int,
    _: User = Depends(require_superuser),
    session: Session = Depends(get_session),
):
    return admin_service.promote_to_admin(session=session, user_id=user_id)


@router.post("/users/{user_id}/demote", response_model=AdminUserRead)
def demote_user(
    user_id: int,
    _: User = Depends(require_superuser),
    session: Session = Depends(get_session),
):
    return admin_service.demote_admin(session=session, user_id=user_id)


@router.get("/instances", response_model=list[InstanceRead])
def list_instances(
    _: User = Depends(require_admin_or_superuser),
    session: Session = Depends(get_session),
    tenant_id: int | None = Query(default=None, ge=1),
    status_filter: InstanceStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    instances = admin_service.list_instances(
        session=session,
        tenant_id=tenant_id,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )
    return [_build_instance_read(item) for item in instances]


@router.post("/instances/{instance_id}/action", response_model=InstanceRead)
def instance_action(
    instance_id: int,
    payload: InstanceActionRequest,
    _: User = Depends(require_admin_or_superuser),
    session: Session = Depends(get_session),
):
    instance = admin_service.apply_instance_action(
        session=session,
        instance_id=instance_id,
        action=payload.action,
    )
    return _build_instance_read(instance)


@router.delete(
    "/instances/{instance_id}",
    status_code=status.HTTP_200_OK,
    response_model=MessageResponse,
)
def delete_instance(
    instance_id: int,
    _: User = Depends(require_admin_or_superuser),
    session: Session = Depends(get_session),
):
    admin_service.delete_instance(session=session, instance_id=instance_id)
    return MessageResponse(message="Instance deleted")


@router.get("/deployments", response_model=list[DeploymentStatusResponse])
def list_deployments(
    _: User = Depends(require_admin_or_superuser),
    session: Session = Depends(get_session),
    tenant_id: int | None = Query(default=None, ge=1),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return admin_service.list_deployments(
        session=session,
        tenant_id=tenant_id,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )


@router.get("/billing/usage", response_model=list[AdminUsageTenantRead])
def list_billing_usage(
    _: User = Depends(require_admin_or_superuser),
    session: Session = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return admin_service.list_usage_by_tenants(session=session, limit=limit, offset=offset)
