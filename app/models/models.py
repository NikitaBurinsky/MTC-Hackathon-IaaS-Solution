from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy import Enum as SQLEnum
from sqlmodel import Field, SQLModel

from app.models.enums import (
    InstanceOperationStatus,
    InstanceOperationType,
    InstanceStatus,
    ScriptSourceType,
    TaskRunStatus,
    TaskStatus,
)


class Plan(SQLModel, table=True):
    __tablename__ = "plans"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(
        sa_column=Column(String(80), unique=True, nullable=False, index=True)
    )
    max_cpu: int = Field(sa_column=Column(Integer, nullable=False))
    max_ram_mb: int = Field(sa_column=Column(Integer, nullable=False))
    created_at: datetime = Field(
        default_factory=datetime.utcnow, sa_column=Column(DateTime, nullable=False)
    )


class Tenant(SQLModel, table=True):
    __tablename__ = "tenants"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(
        sa_column=Column(String(120), unique=True, nullable=False, index=True)
    )
    balance_credits: float = Field(
        default=100.0, sa_column=Column(Float, nullable=False)
    )
    plan_id: int = Field(foreign_key="plans.id", index=True, nullable=False)
    created_at: datetime = Field(
        default_factory=datetime.utcnow, sa_column=Column(DateTime, nullable=False)
    )


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenants.id", index=True, nullable=False)
    name: str = Field(
        sa_column=Column(String(120), unique=True, nullable=False, index=True)
    )
    email: str = Field(
        sa_column=Column(String(255), unique=True, nullable=False, index=True)
    )
    password_hash: str = Field(sa_column=Column(String(255), nullable=False))
    is_active: bool = Field(default=True, nullable=False)
    created_at: datetime = Field(
        default_factory=datetime.utcnow, sa_column=Column(DateTime, nullable=False)
    )


Index("ix_users_email_lower", func.lower(User.email), unique=True)


class Flavor(SQLModel, table=True):
    __tablename__ = "flavors"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(
        sa_column=Column(String(80), unique=True, nullable=False, index=True)
    )
    cpu: int = Field(sa_column=Column(Integer, nullable=False))
    ram_mb: int = Field(sa_column=Column(Integer, nullable=False))
    price_per_minute: float = Field(sa_column=Column(Float, nullable=False))
    created_at: datetime = Field(
        default_factory=datetime.utcnow, sa_column=Column(DateTime, nullable=False)
    )


class Image(SQLModel, table=True):
    __tablename__ = "images"

    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(
        sa_column=Column(String(80), unique=True, nullable=False, index=True)
    )
    docker_image_ref: str = Field(sa_column=Column(String(255), nullable=False))
    display_name: str = Field(sa_column=Column(String(120), nullable=False))
    is_active: bool = Field(default=True, nullable=False)
    created_at: datetime = Field(
        default_factory=datetime.utcnow, sa_column=Column(DateTime, nullable=False)
    )


class Instance(SQLModel, table=True):
    __tablename__ = "instances"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenants.id", index=True, nullable=False)
    name: str = Field(sa_column=Column(String(120), nullable=False))
    flavor_id: int = Field(foreign_key="flavors.id", nullable=False)
    image_id: int = Field(foreign_key="images.id", nullable=False)
    status: InstanceStatus = Field(
        default=InstanceStatus.PROVISIONING,
        sa_column=Column(SQLEnum(InstanceStatus), nullable=False),
    )
    docker_container_id: Optional[str] = Field(
        default=None, sa_column=Column(String(128), nullable=True, index=True)
    )
    ip_address: Optional[str] = Field(
        default=None, sa_column=Column(String(64), nullable=True)
    )
    ssh_port: int = Field(sa_column=Column(Integer, nullable=False))
    ssh_username: str = Field(sa_column=Column(String(120), nullable=False))
    postgres_username: Optional[str] = Field(
        default=None, sa_column=Column(String(120), nullable=True)
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow, sa_column=Column(DateTime, nullable=False)
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow, sa_column=Column(DateTime, nullable=False)
    )
    deleted_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )


class InstanceOperation(SQLModel, table=True):
    __tablename__ = "instance_operations"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenants.id", index=True, nullable=False)
    instance_id: Optional[int] = Field(
        default=None, foreign_key="instances.id", nullable=True, index=True
    )
    type: InstanceOperationType = Field(
        default=InstanceOperationType.CREATE,
        sa_column=Column(SQLEnum(InstanceOperationType), nullable=False),
    )
    status: InstanceOperationStatus = Field(
        default=InstanceOperationStatus.PENDING,
        sa_column=Column(SQLEnum(InstanceOperationStatus), nullable=False),
    )
    error_message: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow, sa_column=Column(DateTime, nullable=False)
    )
    finished_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )


Index("ix_instances_tenant_name", Instance.tenant_id, Instance.name, unique=True)


class ResourceUsageLog(SQLModel, table=True):
    __tablename__ = "resource_usage_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenants.id", index=True, nullable=False)
    instance_id: int = Field(foreign_key="instances.id", index=True, nullable=False)
    flavor_id: int = Field(foreign_key="flavors.id", nullable=False)
    cpu_usage_vcpu: float = Field(default=0.0, sa_column=Column(Float, nullable=False))
    ram_usage_gb: float = Field(default=0.0, sa_column=Column(Float, nullable=False))
    base_price_per_min: float = Field(
        default=0.0, sa_column=Column(Float, nullable=False)
    )
    cpu_charge: float = Field(default=0.0, sa_column=Column(Float, nullable=False))
    ram_charge: float = Field(default=0.0, sa_column=Column(Float, nullable=False))
    total_charge: float = Field(default=0.0, sa_column=Column(Float, nullable=False))
    slice_started_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )
    slice_ended_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )
    started_at: datetime = Field(
        default_factory=datetime.utcnow, sa_column=Column(DateTime, nullable=False)
    )
    ended_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )
    duration_sec: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    credits_charged: float = Field(default=0.0, sa_column=Column(Float, nullable=False))


class Script(SQLModel, table=True):
    __tablename__ = "scripts"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenants.id", index=True, nullable=False)
    name: str = Field(sa_column=Column(String(120), nullable=False))
    body: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        default_factory=datetime.utcnow, sa_column=Column(DateTime, nullable=False)
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow, sa_column=Column(DateTime, nullable=False)
    )


Index("ix_scripts_tenant_name", Script.tenant_id, Script.name, unique=True)


class Task(SQLModel, table=True):
    __tablename__ = "tasks"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenants.id", index=True, nullable=False)
    status: TaskStatus = Field(
        default=TaskStatus.PENDING,
        sa_column=Column(SQLEnum(TaskStatus), nullable=False),
    )
    requested_by_user_id: int = Field(foreign_key="users.id", nullable=False)
    script_source_type: ScriptSourceType = Field(
        default=ScriptSourceType.BODY,
        sa_column=Column(SQLEnum(ScriptSourceType), nullable=False),
    )
    script_body_snapshot: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        default_factory=datetime.utcnow, sa_column=Column(DateTime, nullable=False)
    )
    finished_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )


class TaskRun(SQLModel, table=True):
    __tablename__ = "task_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="tasks.id", index=True, nullable=False)
    instance_id: int = Field(foreign_key="instances.id", index=True, nullable=False)
    status: TaskRunStatus = Field(
        default=TaskRunStatus.PENDING,
        sa_column=Column(SQLEnum(TaskRunStatus), nullable=False),
    )
    stdout: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    stderr: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    started_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )
    finished_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )


class Network(SQLModel, table=True):
    __tablename__ = "networks"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenants.id", index=True, nullable=False)
    name: str = Field(sa_column=Column(String(120), nullable=False))
    cidr: str = Field(sa_column=Column(String(64), nullable=False))
    description: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow, sa_column=Column(DateTime, nullable=False)
    )


Index("ix_networks_tenant_name", Network.tenant_id, Network.name, unique=True)
