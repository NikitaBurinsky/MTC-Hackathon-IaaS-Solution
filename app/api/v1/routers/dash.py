from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.core.deps import get_current_user
from app.db.session import get_session
from app.models import Flavor, Image, Instance, Plan, Tenant, User
from app.schemas import DashUserResponse

router = APIRouter(prefix="/dash", tags=["dash"])


@router.get("/user", response_model=DashUserResponse)
def get_user_dashboard(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    tenant = session.get(Tenant, current_user.tenant_id)
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

    return DashUserResponse(
        user={"email": current_user.email, "name": current_user.name},
        tenant={
            "name": tenant.name,
            "balance": tenant.balance_credits,
            "plan": {
                "id": plan.id,
                "name": plan.name,
                "max_cpu": plan.max_cpu,
                "max_ram_mb": plan.max_ram_mb,
            },
            "instances": instances,
        },
        images=images,
        flavors=flavors,
    )
