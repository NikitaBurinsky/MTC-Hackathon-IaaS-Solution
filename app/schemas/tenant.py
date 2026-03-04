from datetime import datetime

from sqlmodel import SQLModel


class PlanSummary(SQLModel):
    id: int
    name: str
    max_cpu: int
    max_ram_mb: int


class TenantProfileResponse(SQLModel):
    tenant_id: int
    name: str
    balance_credits: float
    plan: PlanSummary
    created_at: datetime
