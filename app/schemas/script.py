from datetime import datetime

from sqlmodel import Field, SQLModel


class ScriptCreateRequest(SQLModel):
    name: str = Field(min_length=1)
    body: str = Field(min_length=1)


class ScriptUpdateRequest(SQLModel):
    name: str | None = Field(default=None, min_length=1)
    body: str | None = Field(default=None, min_length=1)


class ScriptRead(SQLModel):
    id: int
    tenant_id: int
    name: str
    body: str
    created_at: datetime
    updated_at: datetime
