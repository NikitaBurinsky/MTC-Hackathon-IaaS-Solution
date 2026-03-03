from datetime import datetime

from fastapi import BackgroundTasks, HTTPException, status
from sqlmodel import Session, select

from app.db.session import engine
from app.models import (
    Instance,
    InstanceStatus,
    Script,
    ScriptSourceType,
    Task,
    TaskRun,
    TaskRunStatus,
    TaskStatus,
    User,
)
from app.providers.compute.docker_provider import get_docker_provider


class TaskService:
    def create_task(
        self,
        session: Session,
        background_tasks: BackgroundTasks,
        tenant_id: int,
        user: User,
        instance_ids: list[int],
        script_body: str | None,
        script_id: int | None,
    ) -> Task:
        if bool(script_body) == bool(script_id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Provide exactly one of script_body or script_id",
            )

        if not instance_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="instance_ids must not be empty",
            )

        if script_id:
            script = session.exec(
                select(Script).where(Script.id == script_id, Script.tenant_id == tenant_id),
            ).first()
            if not script:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Script not found")
            source = ScriptSourceType.SCRIPT_ID
            body = script.body
        else:
            source = ScriptSourceType.BODY
            body = script_body or ""

        task = Task(
            tenant_id=tenant_id,
            status=TaskStatus.PENDING,
            requested_by_user_id=user.id,
            script_source_type=source,
            script_body_snapshot=body,
        )
        session.add(task)
        session.flush()

        instances = session.exec(
            select(Instance).where(Instance.id.in_(instance_ids), Instance.tenant_id == tenant_id),
        ).all()
        found_ids = {item.id for item in instances}
        missing = [instance_id for instance_id in instance_ids if instance_id not in found_ids]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Instances not found: {missing}",
            )

        for instance_id in instance_ids:
            session.add(
                TaskRun(
                    task_id=task.id,
                    instance_id=instance_id,
                    status=TaskRunStatus.PENDING,
                ),
            )

        session.commit()
        session.refresh(task)
        background_tasks.add_task(self._run_task, task.id)
        return task

    def _run_task(self, task_id: int) -> None:
        provider = get_docker_provider()
        with Session(engine) as session:
            task = session.get(Task, task_id)
            if not task:
                return

            task.status = TaskStatus.RUNNING
            session.add(task)
            session.commit()

            runs = session.exec(select(TaskRun).where(TaskRun.task_id == task.id)).all()
            for run in runs:
                run.started_at = datetime.utcnow()
                run.status = TaskRunStatus.RUNNING
                session.add(run)
                session.commit()

                instance = session.get(Instance, run.instance_id)
                if (
                    not instance
                    or instance.status != InstanceStatus.RUNNING
                    or not instance.docker_container_id
                ):
                    run.status = TaskRunStatus.FAILED
                    run.stderr = "Instance is not RUNNING"
                    run.finished_at = datetime.utcnow()
                    session.add(run)
                    session.commit()
                    continue

                try:
                    exit_code, stdout, stderr = provider.exec_script(
                        instance.docker_container_id,
                        task.script_body_snapshot,
                    )
                    run.stdout = stdout
                    run.stderr = stderr
                    run.status = TaskRunStatus.SUCCESS if exit_code == 0 else TaskRunStatus.FAILED
                except Exception as exc:  # noqa: BLE001
                    run.status = TaskRunStatus.FAILED
                    run.stderr = str(exc)
                run.finished_at = datetime.utcnow()
                session.add(run)
                session.commit()

            runs = session.exec(select(TaskRun).where(TaskRun.task_id == task.id)).all()
            success_count = sum(item.status == TaskRunStatus.SUCCESS for item in runs)
            failed_count = sum(item.status == TaskRunStatus.FAILED for item in runs)

            if failed_count and success_count:
                task.status = TaskStatus.PARTIAL_SUCCESS
            elif failed_count and not success_count:
                task.status = TaskStatus.FAILED
            else:
                task.status = TaskStatus.SUCCESS
            task.finished_at = datetime.utcnow()
            session.add(task)
            session.commit()

    def list_tasks(self, session: Session, tenant_id: int) -> list[Task]:
        return session.exec(
            select(Task).where(Task.tenant_id == tenant_id).order_by(Task.created_at.desc()),
        ).all()

    def get_task(self, session: Session, tenant_id: int, task_id: int) -> Task:
        task = session.exec(
            select(Task).where(Task.id == task_id, Task.tenant_id == tenant_id),
        ).first()
        if not task:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
        return task

    def list_task_runs(self, session: Session, task_id: int) -> list[TaskRun]:
        return session.exec(select(TaskRun).where(TaskRun.task_id == task_id)).all()

