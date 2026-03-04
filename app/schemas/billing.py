from datetime import datetime

from sqlmodel import SQLModel


class QuotaResponse(SQLModel):
    tenant_id: int
    plan_name: str
    max_cpu: int
    max_ram_mb: int
    used_cpu: int
    used_ram_mb: int
    balance_credits: float


class UsageItem(SQLModel):
    id: int
    instance_id: int
    flavor_id: int
    started_at: datetime
    ended_at: datetime | None
    duration_sec: int
    credits_charged: float


class UsageResponse(SQLModel):
    total_charged: float
    items: list[UsageItem]
