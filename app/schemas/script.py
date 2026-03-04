from datetime import datetime

from sqlmodel import SQLModel


class ScriptCreateRequest(SQLModel):
    name: str
    body: str


class ScriptUpdateRequest(SQLModel):
    name: str | None = None
    body: str | None = None


class ScriptRead(SQLModel):
    id: int
    tenant_id: int
    name: str
    body: str
    created_at: datetime
    updated_at: datetime
