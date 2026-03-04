from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.deps import get_current_tenant_id
from app.db.session import get_session
from app.schemas import TenantProfileResponse
from app.services import TenantService

router = APIRouter(prefix="/tenant", tags=["tenant"])
tenant_service = TenantService()


@router.get("/profile", response_model=TenantProfileResponse)
def get_profile(
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    return tenant_service.get_profile(session, tenant_id)
