from typing import Any

from sqlmodel import SQLModel


class MessageResponse(SQLModel):
    message: str


class ErrorDetail(SQLModel):
    code: str
    message: str
    details: dict | None = None


class ErrorResponse(SQLModel):
    error: ErrorDetail


class ErrorResponse(SQLModel):
    detail: Any
