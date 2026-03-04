from typing import Any

from sqlmodel import SQLModel


class MessageResponse(SQLModel):
    message: str


class ErrorDetail(SQLModel):
    code: str
    message: str


class ErrorResponse(SQLModel):
    error: ErrorDetail


class ErrorResponse(SQLModel):
    detail: Any
