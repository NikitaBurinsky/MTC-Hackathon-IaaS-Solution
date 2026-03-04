from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models import Plan, Tenant, User


class AuthService:
    def register(
        self, session: Session, name: str, email: str, password: str, tenant_name: str
    ) -> tuple[User, Tenant]:
        normalized_name = name.strip()
        normalized_email = email.strip().lower()
        normalized_tenant = tenant_name.strip()
        if not normalized_name or not normalized_tenant:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Username and tenant name are required",
            )

        existing = session.exec(
            select(User).where(User.email == normalized_email)
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User with this email already exists",
            )

        existing_name = session.exec(
            select(User).where(User.name == normalized_name)
        ).first()
        if existing_name:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User with this name already exists",
            )

        existing_tenant = session.exec(
            select(Tenant).where(Tenant.name == normalized_tenant)
        ).first()
        if existing_tenant:
            tenant = existing_tenant
        else:
            settings = get_settings()
            plan = session.exec(
                select(Plan).where(Plan.name == settings.default_plan_name)
            ).first()
            if not plan:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Default plan is not configured",
                )

            tenant = Tenant(
                name=normalized_tenant,
                balance_credits=settings.initial_credits,
                plan_id=plan.id,
            )
            session.add(tenant)
            session.flush()

        user = User(
            tenant_id=tenant.id,
            name=normalized_name,
            email=normalized_email,
            password_hash=hash_password(password),
            is_active=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        session.refresh(tenant)
        return user, tenant

    def login(self, session: Session, email: str, password: str) -> str:
        normalized_email = email.strip().lower()
        user = session.exec(
            select(User).where(User.email == normalized_email)
        ).first()
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
