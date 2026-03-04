from fastapi import APIRouter, Depends, Response, status
from sqlmodel import Session

from app.core.config import get_settings
from app.core.security import create_access_token
from app.db.session import get_session
from app.schemas import LoginRequest, RegisterRequest, RegisterResponse, TokenResponse
from app.services import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])
auth_service = AuthService()


def set_auth_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    secure = settings.cookie_secure or settings.cookie_samesite == "none"
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite=settings.cookie_samesite,
        secure=secure,
        max_age=settings.access_token_expire_minutes * 60,
        path="/",
    )


@router.post(
    "/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED
)
def register(
    payload: RegisterRequest,
    response: Response,
    session: Session = Depends(get_session),
):
    user, tenant = auth_service.register(
        session=session,
        name=payload.name,
        email=payload.email,
        password=payload.password,
        tenant_name=payload.tenant_name,
    )
    token = create_access_token(subject=str(user.id), tenant_id=tenant.id)
    set_auth_cookie(response, token)
    return RegisterResponse(
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        user_id=user.id,
        email=user.email,
        access_token=token,
    )


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    response: Response,
    session: Session = Depends(get_session),
):
    token = auth_service.login(
        session=session, email=payload.email, password=payload.password
    )
    set_auth_cookie(response, token)
    return TokenResponse(access_token=token)
