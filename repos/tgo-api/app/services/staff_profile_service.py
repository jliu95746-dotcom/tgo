"""Member-owned profile updates and bounded avatar storage."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models import Staff
from app.schemas.staff import StaffProfileUpdate
from app.utils.const import STAFF_AVATAR_MAX_BYTES

logger = get_logger("services.staff_profile")


def update_profile(db: Session, staff: Staff, data: StaffProfileUpdate) -> Staff:
    """Keep credentials, role, tenant and reception settings untouched."""
    staff.nickname = data.nickname
    staff.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(staff)
    return staff


def _avatar_directory(staff: Staff) -> Path:
    return (
        Path(settings.UPLOAD_BASE_DIR).resolve()
        / "staff-avatars"
        / str(staff.project_id)
        / str(staff.id)
    )


def avatar_path(staff: Staff, filename: str) -> Path:
    """Only generated image names are valid storage references."""
    try:
        stem, extension = filename.split(".")
        if UUID(stem).hex != stem or extension not in {"png", "jpg", "webp"}:
            raise ValueError("Invalid avatar reference")
    except (ValueError, AttributeError) as exc:
        raise HTTPException(404, "头像不存在") from exc
    return _avatar_directory(staff) / filename


def _image_extension(content: bytes, mime: str | None) -> str:
    if (
        mime == "image/png"
        and len(content) >= 24
        and content.startswith(b"\x89PNG\r\n\x1a\n")
        and content[12:16] == b"IHDR"
    ):
        return "png"
    if (
        mime == "image/jpeg"
        and content.startswith(b"\xff\xd8\xff")
        and content.endswith(b"\xff\xd9")
    ):
        return "jpg"
    if (
        mime == "image/webp"
        and len(content) >= 16
        and content[:4] == b"RIFF"
        and content[8:12] == b"WEBP"
    ):
        return "webp"
    raise HTTPException(400, "请选择有效的 JPG、PNG 或 WebP 图片")


async def save_avatar(
    db: Session,
    staff: Staff,
    file: UploadFile,
    profile: StaffProfileUpdate | None = None,
) -> Staff:
    """Commit a new avatar and optional nickname together, then clean up."""
    content = await file.read(STAFF_AVATAR_MAX_BYTES + 1)
    if len(content) > STAFF_AVATAR_MAX_BYTES:
        raise HTTPException(413, "头像不能超过 5MB")
    extension = _image_extension(content, file.content_type)
    filename = f"{uuid4().hex}.{extension}"
    destination = avatar_path(staff, filename)
    old_url = staff.avatar_url
    prefix = f"{settings.API_BASE_URL.rstrip('/')}/v1/staff/{staff.id}/avatar/"
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        staff.avatar_url = prefix + filename
        if profile is not None:
            staff.nickname = profile.nickname
        staff.updated_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        db.rollback()
        destination.unlink(missing_ok=True)
        raise
    db.refresh(staff)
    if old_url and old_url.startswith(prefix):
        try:
            avatar_path(staff, old_url.removeprefix(prefix)).unlink(missing_ok=True)
        except (OSError, HTTPException):
            logger.warning(
                "Could not remove previous staff avatar",
                extra={"staff_id": str(staff.id)},
            )
    return staff
