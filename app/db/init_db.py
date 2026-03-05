from sqlmodel import Session, SQLModel, select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import engine
from app.models import Flavor, Image, Plan, User, UserRole


def seed_defaults(session: Session) -> None:
    settings = get_settings()

    plan = session.exec(
        select(Plan).where(Plan.name == settings.default_plan_name),
    ).first()
    if not plan:
        plan = Plan(
            name=settings.default_plan_name,
            max_cpu=settings.default_plan_cpu,
            max_ram_mb=settings.default_plan_ram_mb,
        )
    else:
        plan.max_cpu = settings.default_plan_cpu
        plan.max_ram_mb = settings.default_plan_ram_mb
    session.add(plan)

    flavor = session.exec(
        select(Flavor).where(Flavor.name == settings.default_flavor_name),
    ).first()
    if not flavor:
        flavor = Flavor(
            name=settings.default_flavor_name,
            cpu=settings.default_flavor_cpu,
            ram_mb=settings.default_flavor_ram_mb,
            price_per_minute=settings.default_flavor_rate,
        )
    else:
        flavor.cpu = settings.default_flavor_cpu
        flavor.ram_mb = settings.default_flavor_ram_mb
        flavor.price_per_minute = settings.default_flavor_rate
    session.add(flavor)

    image = session.exec(
        select(Image).where(Image.code == settings.default_image_code)
    ).first()
    if not image:
        image = Image(
            code=settings.default_image_code,
            docker_image_ref=settings.default_image_ref,
            display_name=settings.default_image_name,
            is_active=True,
        )
    else:
        image.docker_image_ref = settings.default_image_ref
        image.display_name = settings.default_image_name
        image.is_active = True
    session.add(image)

    secondary = session.exec(
        select(Image).where(Image.code == settings.secondary_image_code)
    ).first()
    if not secondary:
        secondary = Image(
            code=settings.secondary_image_code,
            docker_image_ref=settings.secondary_image_ref,
            display_name=settings.secondary_image_name,
            is_active=True,
        )
    else:
        secondary.docker_image_ref = settings.secondary_image_ref
        secondary.display_name = settings.secondary_image_name
        secondary.is_active = True
    session.add(secondary)

    postgres = session.exec(
        select(Image).where(Image.code == settings.postgres_image_code)
    ).first()
    if not postgres:
        postgres = Image(
            code=settings.postgres_image_code,
            docker_image_ref=settings.postgres_image_ref,
            display_name=settings.postgres_image_name,
            is_active=True,
        )
    else:
        postgres.docker_image_ref = settings.postgres_image_ref
        postgres.display_name = settings.postgres_image_name
        postgres.is_active = True
    session.add(postgres)

    docker = session.exec(
        select(Image).where(Image.code == settings.docker_image_code)
    ).first()
    if not docker:
        docker = Image(
            code=settings.docker_image_code,
            docker_image_ref=settings.docker_image_ref,
            display_name=settings.docker_image_name,
            is_active=True,
        )
    else:
        docker.docker_image_ref = settings.docker_image_ref
        docker.display_name = settings.docker_image_name
        docker.is_active = True
    session.add(docker)

    session.commit()


def _build_unique_username(session: Session, base_name: str, email: str) -> str:
    candidate = base_name
    suffix = 0
    while True:
        existing = session.exec(select(User).where(User.name == candidate)).first()
        if not existing or existing.email == email:
            return candidate
        suffix += 1
        candidate = f"{base_name}{suffix}"


def seed_superuser(session: Session) -> None:
    settings = get_settings()
    if not settings.superuser_email or not settings.superuser_password:
        raise RuntimeError("SUPERUSER_EMAIL and SUPERUSER_PASSWORD are required")

    superuser_email = settings.superuser_email
    superuser_name = _build_unique_username(
        session=session,
        base_name=settings.superuser_name,
        email=superuser_email,
    )
    hashed_password = hash_password(settings.superuser_password)

    existing = session.exec(select(User).where(User.email == superuser_email)).first()
    if existing:
        existing.name = superuser_name
        existing.password_hash = hashed_password
        existing.role = UserRole.SUPERUSER
        existing.tenant_id = None
        existing.is_active = True
        session.add(existing)
    else:
        session.add(
            User(
                tenant_id=None,
                name=superuser_name,
                email=superuser_email,
                password_hash=hashed_password,
                role=UserRole.SUPERUSER,
                is_active=True,
            )
        )
    session.commit()


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_defaults(session)
        seed_superuser(session)
