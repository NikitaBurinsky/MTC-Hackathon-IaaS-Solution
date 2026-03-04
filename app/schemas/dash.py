from sqlmodel import SQLModel

from app.schemas.catalog import FlavorRead, ImageRead
from app.schemas.compute import InstanceRead
from app.schemas.tenant import PlanSummary


class DashUser(SQLModel):
    email: str
    name: str


class DashTenant(SQLModel):
    name: str
    balance: float
    plan: PlanSummary
    instances: list[InstanceRead]


class DashUserResponse(SQLModel):
    user: DashUser
    tenant: DashTenant
    images: list[ImageRead]
    flavors: list[FlavorRead]
