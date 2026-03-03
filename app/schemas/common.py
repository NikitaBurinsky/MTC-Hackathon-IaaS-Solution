from typing import Any

from sqlmodel import SQLModel


class MessageResponse(SQLModel):
    message: str


class ErrorResponse(SQLModel):
    detail: Any

