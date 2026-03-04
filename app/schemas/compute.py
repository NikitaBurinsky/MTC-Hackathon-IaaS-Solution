from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models import (
    ActionType,
    InstanceOperationStatus,
    InstanceOperationType,
    InstanceStatus,
)


class InstanceCreateRequest(SQLModel):
    name: str = Field(min_length=1, max_length=120)
    flavor_id: int = Field(gt=0)
    image_id: int = Field(gt=0)


class InstanceRead(SQLModel):
    id: int
    tenant_id: int
    name: str
    flavor_id: int
    image_id: int
    status: InstanceStatus
    ip_address: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class InstanceCreateAccepted(SQLModel):
    instance_id: int
    provisioning_operation_id: int
    status: InstanceStatus


class InstanceActionRequest(SQLModel):
    action: ActionType


class InstanceOperationRead(SQLModel):
    id: int
    tenant_id: int
    instance_id: int | None
    type: InstanceOperationType
    status: InstanceOperationStatus
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None
