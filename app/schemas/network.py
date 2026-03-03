from datetime import datetime

from sqlmodel import SQLModel


class NetworkCreateRequest(SQLModel):
    name: str
    cidr: str
    description: str | None = None


class NetworkUpdateRequest(SQLModel):
    name: str | None = None
    cidr: str | None = None
    description: str | None = None


class NetworkRead(SQLModel):
    id: int
    tenant_id: int
    name: str
    cidr: str
    description: str | None
    created_at: datetime

