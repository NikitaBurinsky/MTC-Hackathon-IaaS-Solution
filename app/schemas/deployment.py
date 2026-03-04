from datetime import datetime

from pydantic import field_validator
from sqlmodel import Field, SQLModel


class DeploymentCreateRequest(SQLModel):
    github_url: str
    tenant_id: int = Field(gt=0)

    @field_validator("github_url")
    @classmethod
    def validate_github_url(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("github_url must not be empty")
        return trimmed


class DeploymentCreateResponse(SQLModel):
    deployment_id: str
    status: str


class DeploymentStatusResponse(SQLModel):
    deployment_id: str
    tenant_id: int
    github_url: str
    status: str
    docker_image: str | None
    container_id: str | None
    container_name: str | None
    container_port: int | None
    public_url: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
