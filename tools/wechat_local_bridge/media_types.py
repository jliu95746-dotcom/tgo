"""Bounded local media copied from an explicitly authorized WeChat message."""

from dataclasses import dataclass, field
from typing import Literal

MAX_MEDIA_BYTES = 10 * 1024 * 1024


class MediaNotReady(RuntimeError):
    """The exact message's local media is not ready or cannot be verified."""


@dataclass(frozen=True)
class MediaContent:
    kind: Literal["image", "voice"]
    mime_type: str
    content: bytes = field(repr=False)

    @property
    def filename(self) -> str:
        extensions = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "audio/silk": ".silk",
            "audio/wav": ".wav",
            "application/vnd.wechat.wxgf": ".wxgf",
        }
        return ("微信图片" if self.kind == "image" else "微信语音") + extensions[self.mime_type]


@dataclass(frozen=True)
class UploadedMedia:
    file_id: str
    kind: Literal["image", "voice"]
    url: str = field(repr=False)
