from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.deps import get_current_tenant_id, get_current_user
from app.db.session import get_session
from app.models import Flavor, Image, Instance, Plan, Tenant, User
from app.schemas import DashUserResponse, DeploymentDashRead, InstanceRead
from app.services import DeploymentService

router = APIRouter(prefix="/dash", tags=["dash"])
deployment_service = DeploymentService()


@router.get("/user", response_model=DashUserResponse)
def get_user_dashboard(
    current_user: User = Depends(get_current_user),
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    tenant = session.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
        )
    plan = session.get(Plan, tenant.plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Plan not found",
        )

    instances = session.exec(
        select(Instance)
        .where(Instance.tenant_id == tenant.id)
        .order_by(Instance.created_at.desc()),
    ).all()
    flavors = session.exec(select(Flavor).order_by(Flavor.name.asc())).all()
    images = session.exec(
        select(Image)
        .where(Image.is_active == True)
        .order_by(Image.code.asc())
    ).all()  # noqa: E712
    settings = get_settings()
    instance_reads = [
        InstanceRead(
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
        for instance in instances
    ]
    deployments = [
        DeploymentDashRead(
            deployment_id=item.deployment_id,
            github_url=item.github_url,
            status=item.status,
            public_url=item.public_url,
            error_message=item.error_message,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
        for item in deployment_service.list_deployments(
            tenant_id=tenant.id,
            limit=50,
            offset=0,
        )
    ]

    return DashUserResponse(
        user={"email": current_user.email, "name": current_user.name},
        tenant={
            "id": tenant.id,
            "name": tenant.name,
            "balance": tenant.balance_credits,
            "plan": {
                "id": plan.id,
                "name": plan.name,
                "max_cpu": plan.max_cpu,
                "max_ram_mb": plan.max_ram_mb,
            },
            "instances": instance_reads,
            "deployments": deployments,
        },
        images=images,
        flavors=flavors,
    )
