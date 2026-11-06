"""Private chat attachments with file-bound, revocable download links."""

from datetime import datetime, timedelta, timezone
from collections.abc import Awaitable, Callable
import hashlib
import hmac
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPAuthorizationCredentials
from jose import JWTError, jwt
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.types import Receive, Scope, Send, Message

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.core.security import verify_token
from app.models import ChatFile, Platform, Project, Staff
from app.schemas.chat_file_authority import StaffFileActor, VisitorFileActor
from app.schemas.chat_file_access import ChatFileAccessResponse
from app.schemas.visitor_access import VisitorIMAccessClaims
from app.services.channel_access import require_staff_channel_access
from app.services.chat_service import authenticate_staff_or_platform
from app.services.file_service import get_safe_ascii_filename
from app.services.im_access import (
    authenticate_visitor_im_claims,
    read_visitor_im_access,
)
from app.services.visitor_data_access import (
    authorize_visitor_channel_id,
    require_visitor_data_access,
)

FileActor = StaffFileActor | VisitorFileActor
FileGuard = Callable[[], Awaitable[None]]
FILE_AUDIENCE = "yujian-chat-file"


def _unauthorized() -> TGOAPIException:
    return TGOAPIException(
        "附件访问凭据无效或已过期",
        "UNAUTHORIZED",
        status_code=401,
    )


def _not_found() -> TGOAPIException:
    return TGOAPIException("附件不存在", "NOT_FOUND", status_code=404)


def authenticate_file_actor(
    db: Session,
    credentials: HTTPAuthorizationCredentials | None,
    platform_key: str | None,
) -> FileActor:
    staff, platform = authenticate_staff_or_platform(
        db, credentials, platform_key
    )
    if staff is not None:
        assert credentials is not None
        claims = verify_token(credentials.credentials)
        if claims is None:
            raise _unauthorized()
        expiry = claims.get("exp")
        return StaffFileActor(
            id=staff.id,
            project_id=staff.project_id,
            token_version=staff.token_version,
            username=staff.username,
            expires_at=expiry if type(expiry) is int else None,
        )
    if platform is None:
        raise _unauthorized()
    access = require_visitor_data_access(db, platform, credentials)
    return VisitorFileActor(
        id=access.visitor_id,
        project_id=access.project_id,
        platform_id=access.platform_id,
        im_claims=read_visitor_im_access(access.im_token.get_secret_value()),
        platform_key_digest=hashlib.sha256(
            platform.api_key.encode()
        ).hexdigest(),
    )


def _current_actor(db: Session, actor: FileActor) -> Staff | None:
    if actor.expires_at is not None and actor.expires_at <= int(
        datetime.now(timezone.utc).timestamp()
    ):
        raise _unauthorized()
    project = (
        db.query(Project.id)
        .filter(
            Project.id == actor.project_id,
            Project.deleted_at.is_(None),
        )
        .first()
    )
    if project is None:
        raise _unauthorized()
    if isinstance(actor, StaffFileActor):
        staff: Staff | None = (
            db.query(Staff)
            .filter(
                Staff.id == actor.id,
                Staff.project_id == actor.project_id,
                Staff.deleted_at.is_(None),
                Staff.account_enabled.is_(True),
            )
            .populate_existing()
            .first()
        )
        if staff is None or staff.token_version != actor.token_version:
            raise _unauthorized()
        return staff
    platform: Platform | None = (
        db.query(Platform)
        .filter(
            Platform.id == actor.platform_id,
            Platform.project_id == actor.project_id,
            Platform.type == "website",
            Platform.deleted_at.is_(None),
            Platform.is_active.is_(True),
        )
        .populate_existing()
        .first()
    )
    if platform is None or not hmac.compare_digest(
        hashlib.sha256(platform.api_key.encode()).hexdigest(),
        actor.platform_key_digest,
    ):
        raise _unauthorized()
    visitor = authenticate_visitor_im_claims(
        db, f"{actor.id}-vtr", actor.im_claims
    )
    if (
        visitor.id != actor.id
        or visitor.project_id != actor.project_id
        or visitor.platform_id != actor.platform_id
    ):
        raise _unauthorized()
    return None


async def authorize_file_channel(
    db: Session, actor: FileActor, channel_id: str, channel_type: int
) -> None:
    staff = _current_actor(db, actor)
    if isinstance(actor, StaffFileActor):
        assert staff is not None
        await require_staff_channel_access(db, staff, channel_id, channel_type)
        _current_actor(db, actor)
    else:
        authorize_visitor_channel_id(actor.id, channel_id, channel_type)


def _actor_from_link(db: Session, token: str, file_id: UUID) -> FileActor:
    if not token or len(token) > 16384:
        raise _unauthorized()
    try:
        claims = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            audience=FILE_AUDIENCE,
            options={
                "require_exp": True,
                "require_aud": True,
                "require_sub": True,
            },
        )
        if claims.get("type") != "chat_file_download":
            raise _unauthorized()
        if UUID(claims["file_id"]) != file_id:
            raise _unauthorized()
        actor_id = UUID(claims["sub"])
        project_id = UUID(claims["project_id"])
    except (JWTError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise _unauthorized() from exc
    if claims.get("actor") == "staff":
        try:
            actor: FileActor = StaffFileActor(
                id=actor_id,
                project_id=project_id,
                token_version=claims["token_version"],
                username="",
                expires_at=claims["exp"],
            )
        except (KeyError, ValidationError) as exc:
            raise _unauthorized() from exc
    elif claims.get("actor") == "visitor":
        try:
            actor = VisitorFileActor(
                id=actor_id,
                project_id=project_id,
                platform_id=UUID(claims["platform_id"]),
                im_claims=VisitorIMAccessClaims.model_validate(
                    claims["visitor_scope"]
                ),
                platform_key_digest=claims["platform_key_digest"],
                expires_at=claims["exp"],
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise _unauthorized() from exc
    else:
        raise _unauthorized()  # Old platform-only links cannot bypass visitor ownership.
    _current_actor(db, actor)
    return actor


async def authorize_chat_file(
    db: Session,
    file_id: UUID,
    credentials: HTTPAuthorizationCredentials | None,
    platform_key: str | None,
    file_token: str | None = None,
) -> tuple[ChatFile, FileActor]:
    actor: FileActor | None = None
    if credentials is not None or platform_key:
        actor = authenticate_file_actor(db, credentials, platform_key)
    elif file_token:
        actor = _actor_from_link(db, file_token, file_id)
    if actor is None:
        raise _unauthorized()
    return await authorize_chat_file_actor(db, file_id, actor)


async def authorize_chat_file_actor(
    db: Session, file_id: UUID, actor: FileActor
) -> tuple[ChatFile, FileActor]:
    _current_actor(db, actor)
    file = (
        db.query(ChatFile)
        .filter(
            ChatFile.id == file_id,
            ChatFile.project_id == actor.project_id,
            ChatFile.deleted_at.is_(None),
        )
        .populate_existing()
        .first()
    )
    if file is None:
        raise _not_found()
    await authorize_file_channel(db, actor, file.channel_id, file.channel_type)
    return file, actor


def issue_file_link(
    file: ChatFile,
    actor: FileActor,
) -> ChatFileAccessResponse:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=5)
    if actor.expires_at is not None:
        expires = min(
            expires, datetime.fromtimestamp(actor.expires_at, timezone.utc)
        )
    if isinstance(actor, VisitorFileActor):
        expires = min(
            expires, datetime.fromtimestamp(actor.im_claims.exp, timezone.utc)
        )
    if expires <= now:
        raise _unauthorized()
    actor_claims: dict[str, object] = (
        {"actor": "staff", "token_version": actor.token_version}
        if isinstance(actor, StaffFileActor)
        else {
            "actor": "visitor",
            "platform_id": str(actor.platform_id),
            "platform_key_digest": actor.platform_key_digest,
            "visitor_scope": actor.im_claims.model_dump(mode="json"),
        }
    )
    token = jwt.encode(
        {
            "type": "chat_file_download",
            "aud": FILE_AUDIENCE,
            "sub": str(actor.id),
            "project_id": str(actor.project_id),
            **actor_claims,
            "file_id": str(file.id),
            "iat": int(now.timestamp()),
            "exp": int(expires.timestamp()),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    base = settings.API_BASE_URL.rstrip("/")
    return ChatFileAccessResponse(
        access_url=f"{base}/v1/chat/files/{file.id}?file_token={token}",
        expires_at=expires,
    )


def chat_file_guard(
    db: Session, file: ChatFile, actor: FileActor
) -> FileGuard:
    original = (
        file.project_id,
        file.channel_id,
        file.channel_type,
        file.file_path,
        file.file_type,
        file.file_name,
    )
    file_id = file.id

    async def check() -> None:
        current, _ = await authorize_chat_file_actor(db, file_id, actor)
        if (
            current.project_id,
            current.channel_id,
            current.channel_type,
            current.file_path,
            current.file_type,
            current.file_name,
        ) != original:
            raise _not_found()

    return check


class _CheckedFileResponse(Response):
    def __init__(self, response: Response, guard: FileGuard):
        super().__init__(
            status_code=response.status_code, headers=dict(response.headers)
        )
        self.response = response
        self.guard = guard

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        async def checked_send(message: Message) -> None:
            if message["type"] in (
                "http.response.start",
                "http.response.body",
            ):
                await self.guard()
            await send(message)

        # Kernel pathsend would bypass checks between chunks.

        safe_scope = dict(scope)
        safe_scope["extensions"] = {
            key: value
            for key, value in scope.get("extensions", {}).items()
            if key != "http.response.pathsend"
        }
        await self.response(safe_scope, receive, checked_send)


async def serve_chat_file(file: ChatFile, guard: FileGuard) -> Response:
    name = get_safe_ascii_filename(file.file_name, str(file.id))
    encoded_name = quote(file.file_name, safe="")
    headers = {
        "Content-Disposition": (
            f"inline; filename=\"{name}\"; filename*=UTF-8''{encoded_name}"
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
        await guard()
        return _CheckedFileResponse(
            FileResponse(str(target), media_type=mime, headers=headers), guard
        )
    from app.services.storage import get_storage

    # Private bucket reads are made by the backend; never expose a public URL.

    limit = int(settings.MAX_UPLOAD_SIZE_MB) * 1024 * 1024
    content = await get_storage().read(file.file_path, max_bytes=limit)
    await guard()
    return _CheckedFileResponse(
        Response(content=content, media_type=mime, headers=headers), guard
    )
