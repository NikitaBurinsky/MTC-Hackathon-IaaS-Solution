from datetime import datetime
import ipaddress

from pydantic import field_validator
from sqlmodel import Field, SQLModel


class NetworkCreateRequest(SQLModel):
    name: str = Field(min_length=1, max_length=120)
    cidr: str = Field(min_length=1, max_length=64)
    description: str | None = None

    @field_validator("cidr")
    @classmethod
    def validate_cidr(cls, value: str) -> str:
        try:
            ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            raise ValueError("Invalid CIDR") from exc
        return value


class NetworkUpdateRequest(SQLModel):
    name: str | None = None
    cidr: str | None = None
    description: str | None = None

    @field_validator("cidr")
    @classmethod
    def validate_cidr(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            raise ValueError("Invalid CIDR") from exc
        return value


class NetworkRead(SQLModel):
    id: int
    tenant_id: int
    name: str
    cidr: str
    description: str | None
    created_at: datetime
