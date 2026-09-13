"""Create an isolated project and its first administrator in one DB transaction."""

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import generate_api_key, get_password_hash
from app.models import Platform, Project, Staff, SystemSetup
from app.schemas.registration import RegistrationRequest
from app.schemas.staff import StaffResponse
from app.services.wukongim_client import wukongim_client
from app.utils.const import CHANNEL_TYPE_PROJECT_STAFF
from app.utils.encoding import build_project_staff_channel_id

logger = get_logger("project_registration")


async def register_project_account(
    db: Session, data: RegistrationRequest
) -> StaffResponse:
    if not settings.PUBLIC_REGISTRATION_ENABLED:
        raise HTTPException(403, "Public registration is disabled")
    setup = db.query(SystemSetup).order_by(SystemSetup.created_at.asc()).first()
    if not setup or not setup.is_installed:
        raise HTTPException(409, "Complete system installation before registering")
    existing = (
        db.query(Staff.id).filter(func.lower(Staff.username) == data.username).first()
    )
    if existing:
        raise HTTPException(409, "An account with this email already exists")

    try:
        project = Project(
            name=data.project_name or f"{data.username.split('@')[0]} 的工作空间",
            api_key=generate_api_key(),
        )
        db.add(project)
        db.flush()
        owner = Staff(
            project_id=project.id,
            username=data.username,
            nickname=data.nickname or data.username.split("@")[0],
            password_hash=get_password_hash(data.password),
            role="admin",
            status="offline",
        )
        db.add(owner)
        # No model keys, customers, skills or third-party channel settings are copied.
        db.add(
            Platform(
                project_id=project.id,
                type="website",
                name="网站小部件",
                api_key=generate_api_key(),
                is_active=True,
                ai_mode="off",
                config={
                    "position": "bottom-right",
                    "widget_title": project.name,
                    "welcome_message": "您好！有什么可以帮您？",
                },
            )
        )
        db.flush()
        result = StaffResponse.model_validate(owner)
        # Commit before external I/O: concurrent signup must not hold a unique-index
        # lock while waiting for IM. Login reconciles the project notification channel.
        db.commit()
        return result
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409,
            "Unable to create account; check whether the email is already registered",
        ) from exc
    except Exception as exc:
        db.rollback()
        logger.warning(
            "Project registration could not complete",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            503, "Unable to initialize the project; please try again later"
        ) from exc


async def ensure_project_staff_channel(db: Session, staff: Staff) -> None:
    """Idempotently reconcile the current project's notification membership on login."""
    members = (
        db.query(Staff.id)
        .filter(
            Staff.project_id == staff.project_id,
            Staff.deleted_at.is_(None),
        )
        .all()
    )
    await wukongim_client.create_channel(
        channel_id=build_project_staff_channel_id(staff.project_id),
        channel_type=CHANNEL_TYPE_PROJECT_STAFF,
        subscribers=[f"{member.id}-staff" for member in members],
    )
