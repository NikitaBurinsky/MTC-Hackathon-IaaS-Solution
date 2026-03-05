from datetime import datetime

from pydantic import field_validator
from sqlmodel import Field, SQLModel

from app.models import (
    ActionType,
    InstanceOperationStatus,
    InstanceOperationType,
    InstanceStatus,
)


class InstanceCreateRequest(SQLModel):
    name: str
    flavor_id: int = Field(gt=0)
    image_id: int = Field(gt=0)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("name must not be empty")
        return trimmed


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
    ssh_host: str
    ssh_port: int
    ssh_username: str
    ssh_password: str
    postgres_username: str | None = None
    postgres_password: str | None = None


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
