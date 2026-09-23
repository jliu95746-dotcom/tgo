"""Private chat attachments with file-bound, revocable download links."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.models import ChatFile, Platform, Project, Staff, Visitor
from app.schemas.chat_file_access import ChatFileAccessResponse
from app.services.channel_access import require_staff_channel_access
from app.services.chat_service import authenticate_staff_or_platform
from app.services.file_service import get_safe_ascii_filename
from app.utils.encoding import parse_visitor_channel_id

FileActor = Staff | Platform
FILE_AUDIENCE = "yujian-chat-file"


def _unauthorized() -> TGOAPIException:
    return TGOAPIException(
        "附件访问凭据无效或已过期", "UNAUTHORIZED", status_code=401,
    )


def _not_found() -> TGOAPIException:
    return TGOAPIException("附件不存在", "NOT_FOUND", status_code=404)


def _actor_from_link(db: Session, token: str, file_id: UUID) -> FileActor:
    try:
        claims = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM],
            audience=FILE_AUDIENCE,
            options={"require_exp": True, "require_aud": True,
                     "require_sub": True},
        )
        if claims.get("type") != "chat_file_download":
            raise _unauthorized()
        if UUID(claims["file_id"]) != file_id:
            raise _unauthorized()
        actor_id = UUID(claims["sub"])
        project_id = UUID(claims["project_id"])
    except (JWTError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise _unauthorized() from exc

    actor: FileActor | None = None
    if claims.get("actor") == "staff":
        actor = db.query(Staff).filter(
            Staff.id == actor_id, Staff.project_id == project_id,
            Staff.deleted_at.is_(None),
            Staff.account_enabled.is_(True),
        ).first()
        if actor is not None and claims.get("token_version", 1) != actor.token_version:
            raise _unauthorized()
    elif claims.get("actor") == "platform":
        actor = db.query(Platform).filter(
            Platform.id == actor_id, Platform.project_id == project_id,
            Platform.deleted_at.is_(None), Platform.is_active.is_(True),
        ).first()
    if actor is None:
        raise _unauthorized()
    return actor


async def authorize_chat_file(
    db: Session, file_id: UUID,
    credentials: HTTPAuthorizationCredentials | None,
    platform_key: str | None, file_token: str | None = None,
) -> tuple[ChatFile, FileActor]:
    actor: FileActor | None = None
    if credentials is not None or platform_key:
        staff, platform = authenticate_staff_or_platform(
            db, credentials, platform_key,
        )
        actor = staff or platform
    elif file_token:
        actor = _actor_from_link(db, file_token, file_id)
    if actor is None:
        raise _unauthorized()
    project = db.query(Project.id).filter(
        Project.id == actor.project_id, Project.deleted_at.is_(None),
    ).first()
    if project is None:
        raise _unauthorized()
    file = db.query(ChatFile).filter(
        ChatFile.id == file_id, ChatFile.project_id == actor.project_id,
        ChatFile.deleted_at.is_(None),
    ).first()
    if file is None:
        raise _not_found()
    if isinstance(actor, Staff):
        await require_staff_channel_access(
            db, actor, file.channel_id, file.channel_type,
        )
    else:
        try:
            visitor_id = (
                parse_visitor_channel_id(file.channel_id)
                if file.channel_type == 251
                else UUID(file.channel_id.removesuffix("-vtr"))
            )
        except ValueError as exc:
            raise _not_found() from exc
        if file.channel_type not in (1, 251):
            raise _not_found()
        visitor = db.query(Visitor.id).filter(
            Visitor.id == visitor_id,
            Visitor.project_id == actor.project_id,
            Visitor.platform_id == actor.id, Visitor.deleted_at.is_(None),
        ).first()
        if visitor is None:
            raise _not_found()
    return file, actor


def issue_file_link(
    file: ChatFile, actor: FileActor,
) -> ChatFileAccessResponse:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=5)
    token = jwt.encode(
        {"type": "chat_file_download", "aud": FILE_AUDIENCE,
         "sub": str(actor.id), "project_id": str(actor.project_id),
         "actor": "staff" if isinstance(actor, Staff) else "platform",
         "token_version": actor.token_version if isinstance(actor, Staff) else 1,
         "file_id": str(file.id), "iat": int(now.timestamp()),
         "exp": int(expires.timestamp())},
        settings.SECRET_KEY, algorithm=settings.ALGORITHM,
    )
    base = settings.API_BASE_URL.rstrip("/")
    return ChatFileAccessResponse(
        access_url=f"{base}/v1/chat/files/{file.id}?file_token={token}",
        expires_at=expires,
    )


async def serve_chat_file(file: ChatFile) -> Response:
    name = get_safe_ascii_filename(file.file_name, str(file.id))
    encoded_name = quote(file.file_name, safe="")
    headers = {
        "Content-Disposition": (
            f'inline; filename="{name}"; filename*=UTF-8\'\'{encoded_name}'
        ),
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'",
        "Referrer-Policy": "no-referrer",
    }
    mime = file.file_type or "application/octet-stream"
    if settings.STORAGE_TYPE == "local":
        root = Path(settings.UPLOAD_BASE_DIR).resolve()
        target = (root / file.file_path).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise _not_found() from exc
        if not target.is_file():
            raise _not_found()
        return FileResponse(str(target), media_type=mime, headers=headers)

    from app.services.storage import get_storage

    # Private bucket reads are made by the backend; never expose a public URL.
    limit = int(settings.MAX_UPLOAD_SIZE_MB) * 1024 * 1024
    content = await get_storage().read(file.file_path, max_bytes=limit)
    return Response(content=content, media_type=mime, headers=headers)
