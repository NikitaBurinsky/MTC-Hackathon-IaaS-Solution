from datetime import datetime

from sqlmodel import SQLModel

from app.models import (
    ActionType,
    InstanceOperationStatus,
    InstanceOperationType,
    InstanceStatus,
)


class InstanceCreateRequest(SQLModel):
    name: str
    flavor_id: int
    image_id: int


class InstanceRead(SQLModel):
    id: int
    tenant_id: int
    name: str
    flavor_id: int
    image_id: int
    status: InstanceStatus
    docker_container_id: str | None
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
