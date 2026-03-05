from fastapi import APIRouter, Depends, Request, Response, status
from sqlmodel import Session

from app.core.config import get_settings
from app.core.security import create_access_token
from app.db.session import get_session
from app.schemas import LoginRequest, RegisterRequest, RegisterResponse, TokenResponse
from app.services import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])
auth_service = AuthService()


def _get_request_host(request: Request) -> str:
    forwarded_host = request.headers.get("x-forwarded-host")
    if forwarded_host:
        return forwarded_host.split(",")[0].strip()
    return request.headers.get("host", "")


def _is_localhost(host: str) -> bool:
    hostname = host.split(":")[0].lower()
    return hostname in {"localhost", "127.0.0.1", "[::1]"}


def set_auth_cookie(response: Response, token: str, request: Request) -> None:
    settings = get_settings()
    host = _get_request_host(request)
    is_local = _is_localhost(host)
    samesite = settings.cookie_samesite
    secure = settings.cookie_secure or samesite == "none"
    domain = "formatis.online"
    if is_local:
        samesite = "lax"
        secure = False
        domain = None
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite=samesite,
        secure=secure,
        max_age=settings.access_token_expire_minutes * 60,
        domain=domain,
        path="/",
    )


@router.post(
    "/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED
)
def register(
    payload: RegisterRequest,
    response: Response,
    request: Request,
    session: Session = Depends(get_session),
):
    user, tenant = auth_service.register(
        session=session,
        name=payload.name,
        email=payload.email,
        password=payload.password,
        tenant_name=payload.tenant_name,
    )
    token = create_access_token(
        subject=str(user.id),
        tenant_id=tenant.id,
        role=user.role.value,
    )
    set_auth_cookie(response, token, request)
    return RegisterResponse(
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        user_id=user.id,
        email=user.email,
        role=user.role,
        access_token=token,
    )


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    session: Session = Depends(get_session),
):
    token, user = auth_service.login(
        session=session, email=payload.email, password=payload.password
    )
    set_auth_cookie(response, token, request)
    return TokenResponse(access_token=token, role=user.role)
