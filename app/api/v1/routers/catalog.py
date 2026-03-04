from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.db.session import get_session
from app.models import Flavor, Image, Plan
from app.schemas import FlavorRead, ImageRead, PlanRead

router = APIRouter(tags=["catalog"])


@router.get("/flavors", response_model=list[FlavorRead])
def list_flavors(
    session: Session = Depends(get_session),
):
    return session.exec(select(Flavor).order_by(Flavor.name.asc())).all()


@router.get("/images", response_model=list[ImageRead])
def list_images(
    session: Session = Depends(get_session),
):
    return session.exec(
        select(Image).where(Image.is_active == True).order_by(Image.code.asc())
    ).all()  # noqa: E712


@router.get("/plans", response_model=list[PlanRead])
def list_plans(session: Session = Depends(get_session)):
    return session.exec(select(Plan).order_by(Plan.id.asc())).all()
