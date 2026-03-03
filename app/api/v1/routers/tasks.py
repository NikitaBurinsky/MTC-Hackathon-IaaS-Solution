from fastapi import APIRouter, BackgroundTasks, Depends
from sqlmodel import Session

from app.core.deps import get_current_tenant_id, get_current_user
from app.db.session import get_session
from app.models import User
from app.schemas import TaskDetailResponse, TaskExecuteRequest, TaskRead, TaskRunRead
from app.services import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])
task_service = TaskService()


@router.post("/execute", response_model=TaskRead)
def execute_task(
    payload: TaskExecuteRequest,
    background_tasks: BackgroundTasks,
    tenant_id: int = Depends(get_current_tenant_id),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    task = task_service.create_task(
        session=session,
        background_tasks=background_tasks,
        tenant_id=tenant_id,
        user=current_user,
        instance_ids=payload.instance_ids,
        script_body=payload.script_body,
        script_id=payload.script_id,
    )
    return task


@router.get("", response_model=list[TaskRead])
def list_tasks(
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    return task_service.list_tasks(session, tenant_id)


@router.get("/{task_id}", response_model=TaskDetailResponse)
def get_task(
    task_id: int,
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    task = task_service.get_task(session, tenant_id, task_id)
    runs = task_service.list_task_runs(session, task.id)
    return TaskDetailResponse(
        task=TaskRead(
            id=task.id,
            tenant_id=task.tenant_id,
            status=task.status,
            requested_by_user_id=task.requested_by_user_id,
            script_source_type=task.script_source_type,
            script_body_snapshot=task.script_body_snapshot,
            created_at=task.created_at,
            finished_at=task.finished_at,
        ),
        runs=[
            TaskRunRead(
                id=run.id,
                task_id=run.task_id,
                instance_id=run.instance_id,
                status=run.status,
                stdout=run.stdout,
                stderr=run.stderr,
                started_at=run.started_at,
                finished_at=run.finished_at,
            )
            for run in runs
        ],
    )
