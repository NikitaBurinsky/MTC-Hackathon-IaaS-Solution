from fastapi import HTTPException, status
from sqlalchemy import func
from sqlmodel import Session, select

from app.models import (
    ActionType,
    Deployment,
    DeploymentAttempt,
    Instance,
    InstanceStatus,
    ResourceUsageLog,
    Tenant,
    User,
    UserRole,
)
from app.schemas import (
    AdminOverviewResponse,
    AdminTenantRead,
    AdminUsageTenantRead,
    AdminUserRead,
    DeploymentAttemptRead,
    DeploymentStatusResponse,
)
from app.services.compute_service import ComputeService


class AdminService:
    def __init__(self) -> None:
        self.compute_service = ComputeService()

    def _count_scalar(self, session: Session, statement) -> int:
        raw = session.exec(statement).one()
        if isinstance(raw, tuple):
            value = raw[0]
        elif hasattr(raw, "__getitem__") and not isinstance(raw, (int, float)):
            value = raw[0]
        else:
            value = raw
        return int(value or 0)

    def get_overview(self, session: Session) -> AdminOverviewResponse:
        tenants_total = self._count_scalar(session, select(func.count()).select_from(Tenant))
        users_total = self._count_scalar(session, select(func.count()).select_from(User))
        admins_total = self._count_scalar(
            session,
            select(func.count()).select_from(User).where(User.role == UserRole.ADMIN),
        )
        superusers_total = self._count_scalar(
            session,
            select(func.count())
            .select_from(User)
            .where(User.role == UserRole.SUPERUSER),
        )
        instances_total = self._count_scalar(
            session,
            select(func.count()).select_from(Instance),
        )
        instances_running = self._count_scalar(
            session,
            select(func.count())
            .select_from(Instance)
            .where(Instance.status == InstanceStatus.RUNNING),
        )
        deployments_total = self._count_scalar(
            session,
            select(func.count()).select_from(Deployment),
        )
        deployments_running = self._count_scalar(
            session,
            select(func.count())
            .select_from(Deployment)
            .where(Deployment.status == "running"),
        )
        return AdminOverviewResponse(
            tenants_total=tenants_total,
            users_total=users_total,
            admins_total=admins_total,
            superusers_total=superusers_total,
            instances_total=instances_total,
            instances_running=instances_running,
            deployments_total=deployments_total,
            deployments_running=deployments_running,
        )

    def list_tenants(self, session: Session, limit: int, offset: int) -> list[AdminTenantRead]:
        tenants = session.exec(
            select(Tenant)
            .order_by(Tenant.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return [
            AdminTenantRead(
                id=item.id,
                name=item.name,
                plan_id=item.plan_id,
                balance_credits=item.balance_credits,
                created_at=item.created_at,
            )
            for item in tenants
        ]

    def list_users(
        self,
        session: Session,
        tenant_id: int | None,
        role: UserRole | None,
        limit: int,
        offset: int,
    ) -> list[AdminUserRead]:
        statement = select(User)
        if tenant_id is not None:
            statement = statement.where(User.tenant_id == tenant_id)
        if role is not None:
            statement = statement.where(User.role == role)
        users = session.exec(
            statement.order_by(User.created_at.desc()).offset(offset).limit(limit)
        ).all()
        return [
            AdminUserRead(
                id=item.id,
                tenant_id=item.tenant_id,
                name=item.name,
                email=item.email,
                role=item.role,
                is_active=item.is_active,
                created_at=item.created_at,
            )
            for item in users
        ]

    def promote_to_admin(self, session: Session, user_id: int) -> AdminUserRead:
        user = session.get(User, user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        if user.role == UserRole.SUPERUSER:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="SuperUser role cannot be changed",
            )
        if user.role == UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User is already an Admin",
            )
        if user.tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only tenant-bound users can be promoted to Admin",
            )

        user.role = UserRole.ADMIN
        session.add(user)
        session.commit()
        session.refresh(user)
        return AdminUserRead(
            id=user.id,
            tenant_id=user.tenant_id,
            name=user.name,
            email=user.email,
            role=user.role,
            is_active=user.is_active,
            created_at=user.created_at,
        )

    def demote_admin(self, session: Session, user_id: int) -> AdminUserRead:
        user = session.get(User, user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        if user.role == UserRole.SUPERUSER:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="SuperUser role cannot be changed",
            )
        if user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User is not an Admin",
            )

        user.role = UserRole.USER
        session.add(user)
        session.commit()
        session.refresh(user)
        return AdminUserRead(
            id=user.id,
            tenant_id=user.tenant_id,
            name=user.name,
            email=user.email,
            role=user.role,
            is_active=user.is_active,
            created_at=user.created_at,
        )

    def list_instances(
        self,
        session: Session,
        tenant_id: int | None,
        status_filter: InstanceStatus | None,
        limit: int,
        offset: int,
    ) -> list[Instance]:
        statement = select(Instance)
        if tenant_id is not None:
            statement = statement.where(Instance.tenant_id == tenant_id)
        if status_filter is not None:
            statement = statement.where(Instance.status == status_filter)
        return session.exec(
            statement.order_by(Instance.created_at.desc()).offset(offset).limit(limit)
        ).all()

    def apply_instance_action(
        self,
        session: Session,
        instance_id: int,
        action: ActionType,
    ) -> Instance:
        instance = session.get(Instance, instance_id)
        if not instance:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Instance not found",
            )
        return self.compute_service.apply_action(
            session=session,
            tenant_id=instance.tenant_id,
            instance_id=instance.id,
            action=action,
        )

    def delete_instance(self, session: Session, instance_id: int) -> None:
        instance = session.get(Instance, instance_id)
        if not instance:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Instance not found",
            )
        self.compute_service.delete_instance(
            session=session,
            tenant_id=instance.tenant_id,
            instance_id=instance.id,
        )

    def list_deployments(
        self,
        session: Session,
        tenant_id: int | None,
        status_filter: str | None,
        limit: int,
        offset: int,
    ) -> list[DeploymentStatusResponse]:
        statement = select(Deployment)
        if tenant_id is not None:
            statement = statement.where(Deployment.tenant_id == tenant_id)
        if status_filter:
            statement = statement.where(Deployment.status == status_filter)

        deployments = session.exec(
            statement.order_by(Deployment.created_at.desc()).offset(offset).limit(limit)
        ).all()
        if not deployments:
            return []

        record_ids = [item.id for item in deployments if item.id is not None]
        attempt_map: dict[int, list[DeploymentAttemptRead]] = {}
        if record_ids:
            attempts = session.exec(
                select(DeploymentAttempt)
                .where(DeploymentAttempt.deployment_record_id.in_(record_ids))
                .order_by(
                    DeploymentAttempt.deployment_record_id.asc(),
                    DeploymentAttempt.attempt.asc(),
                )
            ).all()
            for attempt in attempts:
                bucket = attempt_map.setdefault(attempt.deployment_record_id, [])
                bucket.append(
                    DeploymentAttemptRead(
                        attempt=attempt.attempt,
                        status=attempt.status,
                        technology=attempt.technology,
                        dockerfile=attempt.dockerfile,
                        build_error=attempt.build_error,
                        prompt_context_chars=attempt.prompt_context_chars,
                        started_at=attempt.started_at,
                        finished_at=attempt.finished_at,
                    )
                )

        result: list[DeploymentStatusResponse] = []
        for item in deployments:
            deployment_attempts = attempt_map.get(item.id or -1, [])
            result.append(
                DeploymentStatusResponse(
                    deployment_id=item.deployment_id,
                    tenant_id=item.tenant_id,
                    github_url=item.github_url,
                    status=item.status,
                    docker_image=item.docker_image,
                    container_id=item.container_id,
                    container_name=item.container_name,
                    container_port=item.container_port,
                    public_url=item.public_url,
                    error_message=item.error_message,
                    current_attempt=item.current_attempt,
                    max_attempts=item.max_attempts,
                    attempts=deployment_attempts,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                )
            )
        return result

    def list_usage_by_tenants(
        self,
        session: Session,
        limit: int,
        offset: int,
    ) -> list[AdminUsageTenantRead]:
        rows = session.exec(
            select(
                Tenant.id,
                Tenant.name,
                func.coalesce(func.sum(ResourceUsageLog.total_charge), 0.0),
                func.count(ResourceUsageLog.id),
                func.max(ResourceUsageLog.ended_at),
            )
            .join(ResourceUsageLog, ResourceUsageLog.tenant_id == Tenant.id, isouter=True)
            .group_by(Tenant.id, Tenant.name)
            .order_by(Tenant.id.asc())
            .offset(offset)
            .limit(limit)
        ).all()
        return [
            AdminUsageTenantRead(
                tenant_id=int(item[0]),
                tenant_name=str(item[1]),
                total_charged=float(item[2] or 0.0),
                records_count=int(item[3] or 0),
                last_entry_at=item[4],
            )
            for item in rows
        ]
