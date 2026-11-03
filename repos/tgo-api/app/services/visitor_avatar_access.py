"""Current, private visitor images, bound to the saved avatar version."""

from dataclasses import dataclass
import mimetypes
from pathlib import Path
import secrets
import time
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.current_access_route import CurrentAccessResponse
from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.models import Visitor
from app.schemas.staff_data_access import StaffDataAccess
from app.services import visitor_service
from app.services.profile_access import (
    OperationGuard,
    ProfileAccess,
    not_found,
    require_profile_visitor,
)
from app.services.visitor_chat_access import authority_database


@dataclass(frozen=True)
class AvatarImage:
    url: str
    path: Path
    size: int
    modified_ns: int
    inode: int


def avatar_directory(visitor: Visitor) -> Path:
    root = Path(settings.UPLOAD_BASE_DIR).resolve()
    directory = root / "avatars" / str(visitor.project_id) / str(visitor.id)
    # A redirected tenant directory must never become another tenant's storage.
    if directory.resolve() != directory:
        raise not_found()
    return directory


def saved_image(visitor: Visitor) -> AvatarImage:
    url = visitor.avatar_url or ""
    parsed = urlsplit(url)
    if (
        parsed.scheme or parsed.netloc or parsed.fragment
        or parsed.path != f"/v1/visitors/{visitor.id}/avatar"
    ):
        raise not_found()
    directory = avatar_directory(visitor)
    parameters = parse_qs(parsed.query, keep_blank_values=True)
    if parsed.query:
        versions = parameters.get("v", [])
        if set(parameters) != {"v"} or len(versions) != 1:
            raise not_found()
        name = versions[0]
        if not name or Path(name).name != name or "/" in name or "\\" in name:
            raise not_found()
        path = directory / name
    else:
        # Older uploads saved no filename. Serve only their unambiguous image;
        # never guess which of multiple files was actually committed.
        candidates = [p for p in directory.glob("*") if p.is_file() and p.suffix[1:].lower() in visitor_service.AVATAR_ALLOWED_EXTENSIONS]
        if len(candidates) != 1:
            raise not_found()
        path = candidates[0]
    if (
        path.resolve() != path or not path.is_file()
        or path.suffix[1:].lower() not in visitor_service.AVATAR_ALLOWED_EXTENSIONS
    ):
        raise not_found()
    stat = path.stat()
    if stat.st_size <= 0 or stat.st_size > visitor_service.AVATAR_MAX_SIZE_MB * 1024 * 1024:
        raise not_found()
    return AvatarImage(url, path, stat.st_size, stat.st_mtime_ns, stat.st_ino)


def avatar_guard(
    access: ProfileAccess, visitor_id: UUID, original_url: str | None,
    image: AvatarImage | None = None,
) -> OperationGuard:
    def check() -> None:
        with authority_database() as current_db:
            visitor = require_profile_visitor(current_db, access, visitor_id)
            if visitor.avatar_url != original_url:
                raise not_found()
            if image is not None and saved_image(visitor) != image:
                raise not_found()
    return OperationGuard(check)


def avatar_response(
    db: Session, access: ProfileAccess, visitor_id: UUID, version: str | None,
) -> CurrentAccessResponse:
    visitor = require_profile_visitor(db, access, visitor_id)
    image = saved_image(visitor)
    if version is not None and version != image.path.name:
        raise not_found()
    guard = avatar_guard(access, visitor_id, visitor.avatar_url, image)
    guard.check()
    response = FileResponse(
        str(image.path), media_type=mimetypes.guess_type(image.path.name)[0],
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
    return CurrentAccessResponse(response, guard)


def commit_avatar_version(
    db: Session, visitor: Visitor, original_url: str | None,
    original_revision: int, new_url: str, destination: Path,
) -> None:
    """Replace only the avatar version originally observed by this upload."""
    locked_id = db.execute(
        select(Visitor.id).where(
            Visitor.id == visitor.id,
            Visitor.project_id == visitor.project_id,
        ).with_for_update()
    ).scalar_one_or_none()
    if locked_id is None or destination.is_symlink() or not destination.is_file():
        db.rollback()
        raise HTTPException(409, "头像文件或访问状态已改变，请刷新后重试")
    original_version = (
        Visitor.avatar_url.is_(None)
        if original_url is None else Visitor.avatar_url == original_url
    )
    result = db.execute(
        update(Visitor).where(
            Visitor.id == visitor.id,
            Visitor.project_id == visitor.project_id,
            original_version,
            Visitor.access_revision == original_revision,
            Visitor.access_retired_at.is_(None),
            Visitor.deleted_at.is_(None),
        ).values(avatar_url=new_url).execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "头像已更新或访问权限改变，请刷新后重试")
    db.commit()


async def save_avatar(
    db: Session, access: StaffDataAccess, visitor_id: UUID, file: UploadFile,
) -> tuple[Visitor, int, str]:
    visitor = require_profile_visitor(db, access, visitor_id)
    original_url = visitor.avatar_url
    original_revision = visitor.access_revision
    guard = avatar_guard(access, visitor_id, original_url)
    name = visitor_service.sanitize_avatar_filename(file.filename or "avatar.jpg")
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    mime = file.content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
    if extension not in visitor_service.AVATAR_ALLOWED_EXTENSIONS or mime not in visitor_service.AVATAR_ALLOWED_MIME_TYPES:
        raise HTTPException(400, "File type not allowed. Use JPEG, PNG, GIF or WebP")
    directory = avatar_directory(visitor)
    old_image: AvatarImage | None = None
    try:
        old_image = saved_image(visitor)
    except TGOAPIException as exc:
        if exc.status_code != 404:
            raise
    filename = f"{int(time.time() * 1000)}_{secrets.token_hex(8)}.{extension}"
    destination = directory / filename
    directory.mkdir(parents=True, exist_ok=True)
    avatar_directory(visitor)
    total, created, committed = 0, False, False
    try:
        guard.check()
        with destination.open("xb") as output:
            created = True
            while True:
                chunk = await file.read(1024 * 1024)
                guard.check()
                if not chunk:
                    break
                total += len(chunk)
                if total > visitor_service.AVATAR_MAX_SIZE_MB * 1024 * 1024:
                    raise HTTPException(413, f"File too large. Maximum size: {visitor_service.AVATAR_MAX_SIZE_MB}MB")
                output.write(chunk)
        if not total:
            raise HTTPException(400, "Avatar image is empty")
        guard.check()
        new_url = f"/v1/visitors/{visitor_id}/avatar?v={filename}"
        commit_avatar_version(db, visitor, original_url, original_revision, new_url, destination)
        committed = True
        db.refresh(visitor)
    except BaseException:
        if not committed:
            db.rollback()
            if created:
                destination.unlink(missing_ok=True)
        raise
    # Delete only the previously bound object, after the new version commits.
    # Ambiguous legacy objects are left for the persistent cleanup task.
    if old_image is not None and old_image.path != destination:
        try:
            if old_image.path.resolve() == old_image.path:
                old_image.path.unlink(missing_ok=True)
        except OSError:
            pass
    return visitor, total, mime
