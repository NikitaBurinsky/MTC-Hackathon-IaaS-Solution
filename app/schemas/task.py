from datetime import datetime

from sqlmodel import SQLModel

from app.models import ScriptSourceType, TaskRunStatus, TaskStatus


class TaskExecuteRequest(SQLModel):
    instance_ids: list[int]
    script_body: str | None = None
    script_id: int | None = None


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

