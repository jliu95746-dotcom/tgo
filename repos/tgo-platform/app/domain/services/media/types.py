from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID


MediaType = Literal["image", "voice", "video", "file"]


@dataclass(frozen=True)
class ChannelMediaReference:
    """Channel-independent media fields extracted from one inbound message."""

    source_media_id: str
    media_type: MediaType
    supported: bool
    original_filename: str | None = None
    declared_size: int | None = None


WeComMediaReference = ChannelMediaReference


@dataclass(frozen=True)
class MediaEnvelope:
    """Stable media identity shared by every channel-specific downloader."""

    platform_id: UUID
    source_channel: str
    source_inbox_id: UUID
    source_message_id: str
    reference: ChannelMediaReference


@dataclass(frozen=True)
class DownloadedMedia:
    """Validated binary content returned by the WeCom media API."""

    content: bytes
    mime_type: str
    extension: str
    sha256: str


@dataclass(frozen=True)
class StoredMediaObject:
    """Metadata returned after encrypted media persistence."""

    provider: str
    object_key: str
    encryption_mode: str
    encryption_key_id: str


class MediaDownloadError(RuntimeError):
    """Stable download failure with retry classification."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
