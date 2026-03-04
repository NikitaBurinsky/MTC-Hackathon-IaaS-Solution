from sqlmodel import SQLModel


class RegisterRequest(SQLModel):
    name: str
    email: str
    password: str
    workspace_name: str


class RegisterResponse(SQLModel):
    tenant_id: int
    tenant_name: str
    user_id: int
    email: str
    access_token: str
    token_type: str = "bearer"


class LoginRequest(SQLModel):
    email: str
    password: str


class TokenResponse(SQLModel):
    access_token: str
    token_type: str = "bearer"
