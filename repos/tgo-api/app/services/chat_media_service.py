"""Load only channel-owned media and persist validated recognition results."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.models import ChatFile
from app.schemas.chat_media import ChatMediaInput
from app.services.storage import get_storage
from app.services.storage.base import StorageBackend
from app.utils.encoding import build_visitor_channel_id


class MediaInputError(TGOAPIException):
    def __init__(
        self, message: str = "无法读取这条消息的图片或语音，请重新上传或补充文字说明。"
    ) -> None:
        super().__init__(
            message=message, code="MEDIA_INPUT_UNAVAILABLE", status_code=422
        )


@dataclass(frozen=True)
class LoadedChatMedia:
    file_id: UUID
    mime_type: str
    sha256: str
    content: bytes = field(repr=False)


class ChatMediaService:
    def __init__(self, db: Session, *, storage: StorageBackend | None = None) -> None:
        self._db = db
        self._storage = storage or get_storage()

    async def load(self, request: ChatMediaInput) -> LoadedChatMedia:
        # URLs supply an identifier only. Their host is never contacted.
        try:
            path = urlsplit(request.reference).path
            match = re.fullmatch(r"/(?:api/)?v1/chat/files/([0-9a-fA-F-]{36})", path)
            referenced_id = UUID(match.group(1)) if match else None
        except ValueError as exc:
            raise MediaInputError() from exc
        if request.file_id is not None and referenced_id not in (None, request.file_id):
            raise MediaInputError()
        file_id = request.file_id or referenced_id
        if file_id is None:
            raise MediaInputError()
        row = (
            self._db.query(ChatFile)
            .filter(
                ChatFile.id == file_id,
                ChatFile.project_id == request.project_id,
                ChatFile.channel_id == build_visitor_channel_id(request.visitor_id),
                ChatFile.channel_type == 251,
                ChatFile.deleted_at.is_(None),
            )
            .first()
        )
        if row is None or (
            row.uploaded_by_platform_id is not None
            and row.uploaded_by_platform_id != request.platform_id
        ):
            raise MediaInputError()
        if referenced_id is None and request.reference != self._storage.get_public_url(
            row.file_path
        ):
            raise MediaInputError()
        max_bytes = settings.MULTIMODAL_MAX_MEDIA_BYTES
        mime_type = row.file_type.lower()
        prefix = "image/" if request.message_type == 2 else "audio/"
        if not mime_type.startswith(prefix) or not 0 < row.file_size <= max_bytes:
            raise MediaInputError(
                "文件类型或大小不适合识别，请重新上传或补充文字说明。"
            )
        try:
            content = await self._storage.read(row.file_path, max_bytes=max_bytes)
        except (OSError, ValueError, NotImplementedError) as exc:
            raise MediaInputError() from exc
        if len(content) != row.file_size:
            raise MediaInputError()
        return LoadedChatMedia(
            row.id, mime_type, hashlib.sha256(content).hexdigest(), content
        )
