from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from app.core.security import decode_access_token
from app.db.session import get_session
from app.models import User, UserRole

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    access_token: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    token = credentials.credentials if credentials else access_token
    if not token:
        raise credentials_error

    try:
        payload = decode_access_token(token)
        sub = payload.get("sub")
        tenant_id = payload.get("tenant_id")
        payload_role = payload.get("role")
        if sub is None or payload_role is None:
            raise credentials_error
        user_id = int(sub)
        if tenant_id is not None:
            tenant_id = int(tenant_id)
        payload_role = UserRole(str(payload_role))
    except Exception as exc:  # noqa: BLE001 - map any parse issue to 401
        raise credentials_error from exc

    user = session.get(User, user_id)
    if not user or not user.is_active:
        raise credentials_error
    if payload_role != user.role:
        raise credentials_error

    if user.role in {UserRole.USER, UserRole.ADMIN}:
        if user.tenant_id is None or tenant_id is None or user.tenant_id != tenant_id:
            raise credentials_error
    elif user.role == UserRole.SUPERUSER:
        if user.tenant_id is not None or tenant_id is not None:
            raise credentials_error
    else:
        raise credentials_error
    return user


def get_current_tenant_id(current_user: User = Depends(get_current_user)) -> int:
    if current_user.tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant context is not available for this role",
        )
    return current_user.tenant_id


def require_admin_or_superuser(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role not in {UserRole.ADMIN, UserRole.SUPERUSER}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin permissions required",
        )
    return current_user


def require_superuser(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.SUPERUSER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SuperUser permissions required",
        )
    return current_user
