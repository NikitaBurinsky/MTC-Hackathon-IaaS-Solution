from datetime import datetime

from sqlmodel import SQLModel


class FlavorRead(SQLModel):
    id: int
    name: str
    cpu: int
    ram_mb: int
    price_per_minute: float
    created_at: datetime


class ImageRead(SQLModel):
    id: int
    code: str
    display_name: str
    is_active: bool
    created_at: datetime
