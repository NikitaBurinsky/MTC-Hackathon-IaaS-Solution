from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from app.core.security import decode_access_token
from app.db.session import get_session
from app.models import User

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
        if sub is None or tenant_id is None:
            raise credentials_error
        user_id = int(sub)
        tenant_id = int(tenant_id)
    except Exception as exc:  # noqa: BLE001 - map any parse issue to 401
        raise credentials_error from exc

    user = session.get(User, user_id)
    if not user or not user.is_active or user.tenant_id != tenant_id:
        raise credentials_error
    return user


def get_current_tenant_id(current_user: User = Depends(get_current_user)) -> int:
    return current_user.tenant_id
