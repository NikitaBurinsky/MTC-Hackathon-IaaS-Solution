import re

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models import Plan, Tenant, User


class AuthService:
    @staticmethod
    def _tenant_name_from_email(email: str) -> str:
        local = email.split("@", 1)[0].lower()
        slug = re.sub(r"[^a-z0-9-]", "-", local).strip("-")
        slug = slug or "org"
        return f"tenant-{slug}"

    def register(self, session: Session, email: str, password: str) -> tuple[User, Tenant]:
        existing = session.exec(select(User).where(User.email == email)).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User with this email already exists",
            )

        settings = get_settings()
        plan = session.exec(select(Plan).where(Plan.name == settings.default_plan_name)).first()
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Default plan is not configured",
            )

        tenant_name = self._tenant_name_from_email(email)
        suffix = 1
        unique_name = tenant_name
        while session.exec(select(Tenant).where(Tenant.name == unique_name)).first():
            suffix += 1
            unique_name = f"{tenant_name}-{suffix}"

        tenant = Tenant(
            name=unique_name,
            balance_credits=settings.initial_credits,
            plan_id=plan.id,
        )
        session.add(tenant)
        session.flush()

        user = User(
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password(password),
            is_active=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        session.refresh(tenant)
        return user, tenant

    def login(self, session: Session, email: str, password: str) -> str:
        user = session.exec(select(User).where(User.email == email)).first()
        if not user or not verify_password(password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Inactive user",
            )
        return create_access_token(subject=str(user.id), tenant_id=user.tenant_id)

