import logging
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, text
from sqlmodel import Session, select

from app.core.config import get_settings
from app.models import Flavor, Instance, InstanceStatus, Plan, ResourceUsageLog, Tenant
from app.providers.compute.docker_provider import get_docker_provider

logger = logging.getLogger(__name__)


class BillingService:
    BILLING_LOCK_ID = 937163
    CHARGE_ROUND_DECIMALS = 6

    def get_tenant_and_plan(
        self, session: Session, tenant_id: int
    ) -> tuple[Tenant, Plan]:
        tenant = session.get(Tenant, tenant_id)
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
            )
        plan = session.get(Plan, tenant.plan_id)
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Plan not found",
            )
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

    def assert_can_allocate(
        self, session: Session, tenant_id: int, flavor: Flavor
    ) -> None:
        quota = self.get_quota(session, tenant_id)
        if quota["balance_credits"] <= 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Insufficient credits to create a new instance",
            )
        if quota["used_cpu"] + flavor.cpu > quota["max_cpu"]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="CPU quota exceeded"
            )
        if quota["used_ram_mb"] + flavor.ram_mb > quota["max_ram_mb"]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="RAM quota exceeded"
            )

    def run_realtime_billing(self, session: Session, interval_sec: int = 60) -> None:
        if not self._try_acquire_lock(session):
            return
        try:
            self._bill_running_instances(session, interval_sec)
        finally:
            self._release_lock(session)

    def _try_acquire_lock(self, session: Session) -> bool:
        try:
            result = session.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": self.BILLING_LOCK_ID},
            ).one()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to acquire billing lock")
            return False
        return bool(result[0])

    def _release_lock(self, session: Session) -> None:
        try:
            session.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": self.BILLING_LOCK_ID},
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to release billing lock")

    def _bill_running_instances(self, session: Session, interval_sec: int) -> None:
        now = datetime.utcnow()
        running_instances = session.exec(
            select(Instance).where(Instance.status == InstanceStatus.RUNNING),
        ).all()
        if not running_instances:
            return
        provider = get_docker_provider()
        settings = get_settings()
        stopped_tenants: set[int] = set()
        for instance in running_instances:
            if instance.tenant_id in stopped_tenants:
                continue
            if not instance.docker_container_id:
                self._mark_instance_stopped(session, instance)
                session.commit()
                continue
            flavor = session.get(Flavor, instance.flavor_id)
            if not flavor:
                continue
            try:
                stats = provider.get_instance_stats(instance.docker_container_id)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to read stats for billing instance_id=%s", instance.id
                )
                continue
            if stats is None:
                self._mark_instance_stopped(session, instance)
                session.commit()
                continue
            slice_start, duration_sec = self._resolve_slice_window(
                session, instance, now, interval_sec
            )
            if duration_sec <= 0:
                continue
            cpu_usage_vcpu, ram_usage_gb = self._extract_usage(stats, flavor)
            base_charge, cpu_charge, ram_charge, total_charge = (
                self._calculate_charges(
                    settings,
                    flavor,
                    cpu_usage_vcpu,
                    ram_usage_gb,
                    duration_sec,
                )
            )
            log = ResourceUsageLog(
                tenant_id=instance.tenant_id,
                instance_id=instance.id,
                flavor_id=instance.flavor_id,
                cpu_usage_vcpu=cpu_usage_vcpu,
                ram_usage_gb=ram_usage_gb,
                base_price_per_min=flavor.price_per_minute,
                cpu_charge=cpu_charge,
                ram_charge=ram_charge,
                total_charge=total_charge,
                slice_started_at=slice_start,
                slice_ended_at=now,
                started_at=slice_start,
                ended_at=now,
                duration_sec=duration_sec,
                credits_charged=total_charge,
            )
            tenant = session.get(Tenant, instance.tenant_id)
            if not tenant:
                continue
            tenant.balance_credits = round(
                float(tenant.balance_credits) - total_charge,
                self.CHARGE_ROUND_DECIMALS,
            )
            session.add(log)
            session.add(tenant)
            session.commit()
            if tenant.balance_credits <= 0:
                self._stop_tenant_instances(session, tenant.id, provider)
                session.commit()
                stopped_tenants.add(tenant.id)

    def _resolve_slice_window(
        self,
        session: Session,
        instance: Instance,
        now: datetime,
        interval_sec: int,
    ) -> tuple[datetime, int]:
        last_slice = session.exec(
            select(ResourceUsageLog.slice_ended_at)
            .where(
                ResourceUsageLog.instance_id == instance.id,
                ResourceUsageLog.tenant_id == instance.tenant_id,
                ResourceUsageLog.slice_ended_at.is_not(None),
            )
            .order_by(ResourceUsageLog.slice_ended_at.desc()),
        ).first()
        start_at = last_slice or instance.updated_at
        if start_at is None:
            start_at = now
        if start_at > now:
            start_at = now
        duration_sec = int((now - start_at).total_seconds())
        if duration_sec <= 0:
            return now, 0
        if duration_sec > interval_sec:
            duration_sec = interval_sec
            start_at = now - timedelta(seconds=duration_sec)
        return start_at, duration_sec

    def _extract_usage(self, stats: dict, flavor: Flavor) -> tuple[float, float]:
        cpu_percent = self._calculate_cpu_percent(stats)
        cpu_usage_vcpu = max(cpu_percent / 100.0, 0.0)
        if flavor.cpu:
            cpu_usage_vcpu = min(cpu_usage_vcpu, float(flavor.cpu))
        memory_usage = (stats.get("memory_stats") or {}).get("usage", 0)
        ram_usage_gb = max(memory_usage / (1024**3), 0.0)
        return cpu_usage_vcpu, ram_usage_gb

    def _calculate_cpu_percent(self, stats: dict) -> float:
        cpu_stats = stats.get("cpu_stats") or {}
        precpu_stats = stats.get("precpu_stats") or {}
        cpu_usage = cpu_stats.get("cpu_usage") or {}
        precpu_usage = precpu_stats.get("cpu_usage") or {}
        cpu_delta = cpu_usage.get("total_usage", 0) - precpu_usage.get("total_usage", 0)
        system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get(
            "system_cpu_usage", 0
        )
        online_cpus = cpu_stats.get("online_cpus") or len(
            cpu_usage.get("percpu_usage", []) or []
        )
        if cpu_delta <= 0 or system_delta <= 0 or online_cpus <= 0:
            return 0.0
        return (cpu_delta / system_delta) * online_cpus * 100.0

    def _calculate_charges(
        self,
        settings: object,
        flavor: Flavor,
        cpu_usage_vcpu: float,
        ram_usage_gb: float,
        duration_sec: int,
    ) -> tuple[float, float, float, float]:
        duration_min = duration_sec / 60.0
        base_charge = round(
            flavor.price_per_minute * duration_min, self.CHARGE_ROUND_DECIMALS
        )
        cpu_charge = round(
            cpu_usage_vcpu * settings.cpu_price_per_vcpu_min * duration_min,
            self.CHARGE_ROUND_DECIMALS,
        )
        ram_charge = round(
            ram_usage_gb * settings.ram_price_per_gb_min * duration_min,
            self.CHARGE_ROUND_DECIMALS,
        )
        total_charge = round(
            base_charge + cpu_charge + ram_charge, self.CHARGE_ROUND_DECIMALS
        )
        return base_charge, cpu_charge, ram_charge, total_charge

    def _mark_instance_stopped(self, session: Session, instance: Instance) -> None:
        instance.status = InstanceStatus.STOPPED
        instance.docker_container_id = None
        instance.ip_address = None
        instance.updated_at = datetime.utcnow()
        session.add(instance)

    def _stop_tenant_instances(
        self, session: Session, tenant_id: int, provider: object
    ) -> None:
        instances = session.exec(
            select(Instance).where(
                Instance.tenant_id == tenant_id,
                Instance.status == InstanceStatus.RUNNING,
            ),
        ).all()
        if not instances:
            return
        for instance in instances:
            if instance.docker_container_id:
                try:
                    provider.stop_instance(instance.docker_container_id)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "Failed to stop instance for billing tenant_id=%s instance_id=%s",
                        tenant_id,
                        instance.id,
                    )
            instance.status = InstanceStatus.STOPPED
            instance.updated_at = datetime.utcnow()
            session.add(instance)
        logger.warning(
            "Auto-stopped instances due to insufficient credits tenant_id=%s",
            tenant_id,
        )

    def get_usage(self, session: Session, tenant_id: int) -> dict:
        logs = session.exec(
            select(ResourceUsageLog)
            .where(ResourceUsageLog.tenant_id == tenant_id)
            .order_by(ResourceUsageLog.started_at.desc()),
        ).all()
        total_charged = round(
            sum(log.total_charge for log in logs),
            self.CHARGE_ROUND_DECIMALS,
        )
        return {
            "items": logs,
            "total_charged": total_charged,
        }
