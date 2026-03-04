from datetime import datetime

from sqlmodel import SQLModel


class DeploymentCreateRequest(SQLModel):
    github_url: str
    tenant_id: int


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
