from fastapi import APIRouter, Depends, status
from sqlmodel import Session

from app.core.security import create_access_token
from app.db.session import get_session
from app.schemas import LoginRequest, RegisterRequest, RegisterResponse, TokenResponse
from app.services import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])
auth_service = AuthService()


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, session: Session = Depends(get_session)):
    user, tenant = auth_service.register(session=session, email=payload.email, password=payload.password)
    token = create_access_token(subject=str(user.id), tenant_id=tenant.id)
    return RegisterResponse(
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        user_id=user.id,
        email=user.email,
        access_token=token,
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, session: Session = Depends(get_session)):
    token = auth_service.login(session=session, email=payload.email, password=payload.password)
    return TokenResponse(access_token=token)

