from pydantic import field_validator
from sqlmodel import SQLModel

from app.models import UserRole


class RegisterRequest(SQLModel):
    name: str
    email: str
    password: str
    tenant_name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("name must not be empty")
        return trimmed

    @field_validator("tenant_name")
    @classmethod
    def validate_tenant_name(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("tenant_name must not be empty")
        return trimmed

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("email must not be empty")
        return trimmed

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("password must not be empty")
        return value


class RegisterResponse(SQLModel):
    tenant_id: int
    tenant_name: str
    user_id: int
    email: str
    role: UserRole
    access_token: str
    token_type: str = "bearer"


class LoginRequest(SQLModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("email must not be empty")
        return trimmed

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("password must not be empty")
        return value


class TokenResponse(SQLModel):
    access_token: str
    role: UserRole
    token_type: str = "bearer"
