from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlmodel import Session, select

from app.models import Flavor, Instance, InstanceStatus, Plan, ResourceUsageLog, Tenant


class BillingService:
    def get_tenant_and_plan(self, session: Session, tenant_id: int) -> tuple[Tenant, Plan]:
        tenant = session.get(Tenant, tenant_id)
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        plan = session.get(Plan, tenant.plan_id)
        if not plan:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Plan not found")
        return tenant, plan

    def get_quota(self, session: Session, tenant_id: int) -> dict:
        tenant, plan = self.get_tenant_and_plan(session, tenant_id)
        used_stmt = (
            select(
                func.coalesce(func.sum(Flavor.cpu), 0),
                func.coalesce(func.sum(Flavor.ram_mb), 0),
            )
            .join(Instance, Instance.flavor_id == Flavor.id)
            .where(
                Instance.tenant_id == tenant_id,
                Instance.status != InstanceStatus.TERMINATED,
            )
        )
        used_cpu, used_ram = session.exec(used_stmt).one()

        return {
            "tenant_id": tenant.id,
            "plan_name": plan.name,
            "max_cpu": plan.max_cpu,
            "max_ram_mb": plan.max_ram_mb,
            "used_cpu": int(used_cpu or 0),
            "used_ram_mb": int(used_ram or 0),
            "balance_credits": float(tenant.balance_credits),
        }

    def assert_can_allocate(self, session: Session, tenant_id: int, flavor: Flavor) -> None:
        quota = self.get_quota(session, tenant_id)
        if quota["balance_credits"] <= 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Insufficient credits to create a new instance",
            )
        if quota["used_cpu"] + flavor.cpu > quota["max_cpu"]:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="CPU quota exceeded")
        if quota["used_ram_mb"] + flavor.ram_mb > quota["max_ram_mb"]:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="RAM quota exceeded")

    def open_usage_interval(self, session: Session, instance: Instance) -> None:
        session.add(
            ResourceUsageLog(
                tenant_id=instance.tenant_id,
                instance_id=instance.id,
                flavor_id=instance.flavor_id,
                started_at=datetime.utcnow(),
            ),
        )
        session.flush()

    def close_open_usage_interval(self, session: Session, instance: Instance) -> None:
        open_log = session.exec(
            select(ResourceUsageLog)
            .where(
                ResourceUsageLog.instance_id == instance.id,
                ResourceUsageLog.tenant_id == instance.tenant_id,
                ResourceUsageLog.ended_at.is_(None),
            )
            .order_by(ResourceUsageLog.started_at.desc()),
        ).first()
        if not open_log:
            return

        flavor = session.get(Flavor, instance.flavor_id)
        if not flavor:
            return

        ended_at = datetime.utcnow()
        duration_sec = max(0, int((ended_at - open_log.started_at).total_seconds()))
        charged = round((duration_sec / 60.0) * flavor.price_per_minute, 4)

        open_log.ended_at = ended_at
        open_log.duration_sec = duration_sec
        open_log.credits_charged = charged

        tenant = session.get(Tenant, instance.tenant_id)
        if tenant:
            tenant.balance_credits = round(float(tenant.balance_credits) - charged, 4)
            session.add(tenant)
        session.add(open_log)
        session.flush()

    def get_usage(self, session: Session, tenant_id: int) -> dict:
        logs = session.exec(
            select(ResourceUsageLog)
            .where(ResourceUsageLog.tenant_id == tenant_id)
            .order_by(ResourceUsageLog.started_at.desc()),
        ).all()
        total_charged = round(sum(log.credits_charged for log in logs), 4)
        return {
            "items": logs,
            "total_charged": total_charged,
        }

