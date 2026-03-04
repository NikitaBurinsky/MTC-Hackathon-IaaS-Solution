from datetime import datetime

from pydantic import field_validator
from sqlmodel import Field, SQLModel

from app.models import ScriptSourceType, TaskRunStatus, TaskStatus


class TaskExecuteRequest(SQLModel):
    instance_ids: list[int] = Field(min_length=1)
    script_body: str | None = Field(default=None, min_length=1)
    script_id: int | None = Field(default=None, gt=0)

    @field_validator("instance_ids")
    @classmethod
    def validate_instance_ids(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("instance_ids must not be empty")
        return value


class TaskRead(SQLModel):
    id: int
    tenant_id: int
    status: TaskStatus
    requested_by_user_id: int
    script_source_type: ScriptSourceType
    script_body_snapshot: str
    created_at: datetime
    finished_at: datetime | None


class TaskRunRead(SQLModel):
    id: int
    task_id: int
    instance_id: int
    status: TaskRunStatus
    stdout: str | None
    stderr: str | None
    started_at: datetime | None
    finished_at: datetime | None


class TaskDetailResponse(SQLModel):
    task: TaskRead
    runs: list[TaskRunRead]
