from datetime import datetime

from sqlmodel import SQLModel

from app.models import UserRole


class AdminOverviewResponse(SQLModel):
    tenants_total: int
    users_total: int
    admins_total: int
    superusers_total: int
    instances_total: int
    instances_running: int
    deployments_total: int
    deployments_running: int


class AdminTenantRead(SQLModel):
    id: int
    name: str
    plan_id: int
    balance_credits: float
    created_at: datetime


class AdminUserRead(SQLModel):
    id: int
    tenant_id: int | None
    name: str
    email: str
    role: UserRole
    is_active: bool
    created_at: datetime


class AdminUsageTenantRead(SQLModel):
    tenant_id: int
    tenant_name: str
    total_charged: float
    records_count: int
    last_entry_at: datetime | None
