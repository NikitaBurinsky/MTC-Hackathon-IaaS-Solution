from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.deps import get_current_tenant_id
from app.db.session import get_session
from app.schemas import QuotaResponse, UsageItem, UsageResponse
from app.services import BillingService

router = APIRouter(prefix="/billing", tags=["billing"])
billing_service = BillingService()


@router.get("/quotas", response_model=QuotaResponse)
def get_quotas(
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    data = billing_service.get_quota(session, tenant_id)
    return QuotaResponse(**data)


@router.get("/usage", response_model=UsageResponse)
def get_usage(
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    usage = billing_service.get_usage(session, tenant_id)
    items = [
        UsageItem(
            id=item.id,
            instance_id=item.instance_id,
            flavor_id=item.flavor_id,
            started_at=item.started_at,
            ended_at=item.ended_at,
            duration_sec=item.duration_sec,
            cpu_usage_vcpu=item.cpu_usage_vcpu,
            ram_usage_gb=item.ram_usage_gb,
            base_price_per_min=item.base_price_per_min,
            cpu_charge=item.cpu_charge,
            ram_charge=item.ram_charge,
            total_charge=item.total_charge,
        )
        for item in usage["items"]
    ]
    return UsageResponse(total_charged=usage["total_charged"], items=items)
