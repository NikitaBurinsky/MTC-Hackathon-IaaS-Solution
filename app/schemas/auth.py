from sqlmodel import Field, SQLModel


class RegisterRequest(SQLModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=128)
    tenant_name: str = Field(min_length=1, max_length=120)


class RegisterResponse(SQLModel):
    tenant_id: int
    tenant_name: str
    user_id: int
    email: str
    access_token: str
    token_type: str = "bearer"


class LoginRequest(SQLModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(SQLModel):
    access_token: str
    token_type: str = "bearer"
