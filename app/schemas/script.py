from datetime import datetime

from pydantic import field_validator
from sqlmodel import SQLModel


class ScriptCreateRequest(SQLModel):
    name: str
    body: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("name must not be empty")
        return trimmed

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("body must not be empty")
        return value


class ScriptUpdateRequest(SQLModel):
    name: str | None = None
    body: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("name must not be empty")
        return trimmed

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.strip():
            raise ValueError("body must not be empty")
        return value


class ScriptRead(SQLModel):
    id: int
    tenant_id: int
    name: str
    body: str
    created_at: datetime
    updated_at: datetime
