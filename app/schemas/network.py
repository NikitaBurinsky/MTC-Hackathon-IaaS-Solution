from datetime import datetime
import ipaddress

from pydantic import field_validator
from sqlmodel import SQLModel


class NetworkCreateRequest(SQLModel):
    name: str
    cidr: str
    description: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("name must not be empty")
        return trimmed

    @field_validator("cidr")
    @classmethod
    def validate_cidr(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("cidr must not be empty")
        try:
            ipaddress.ip_network(trimmed, strict=False)
        except ValueError as exc:
            raise ValueError("Invalid CIDR") from exc
        return trimmed


class NetworkUpdateRequest(SQLModel):
    name: str | None = None
    cidr: str | None = None
    description: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("name must not be empty")
        return trimmed

    @field_validator("cidr")
    @classmethod
    def validate_cidr(cls, value: str | None) -> str | None:
        if value is None:
            return value
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("cidr must not be empty")
        try:
            ipaddress.ip_network(trimmed, strict=False)
        except ValueError as exc:
            raise ValueError("Invalid CIDR") from exc
        return trimmed


class NetworkRead(SQLModel):
    id: int
    tenant_id: int
    name: str
    cidr: str
    description: str | None
    created_at: datetime
