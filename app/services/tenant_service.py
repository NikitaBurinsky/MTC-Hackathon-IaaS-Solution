from fastapi import HTTPException, status
from sqlmodel import Session

from app.models import Plan, Tenant


class TenantService:
    def get_profile(self, session: Session, tenant_id: int) -> dict:
        tenant = session.get(Tenant, tenant_id)
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        plan = session.get(Plan, tenant.plan_id)
        if not plan:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Plan not found")

        return {
            "tenant_id": tenant.id,
            "name": tenant.name,
            "balance_credits": tenant.balance_credits,
            "plan": {
                "id": plan.id,
                "name": plan.name,
                "max_cpu": plan.max_cpu,
                "max_ram_mb": plan.max_ram_mb,
            },
            "created_at": tenant.created_at,
        }

