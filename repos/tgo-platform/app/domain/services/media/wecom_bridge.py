"""Transfer verified WeCom media into the tenant-scoped chat file store."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

import httpx


MAX_CHAT_MEDIA_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class PreparedMedia:
    content: bytes
    mime_type: str
    filename: str


async def _run_ffmpeg(content: bytes, *arguments: str) -> bytes:
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-i", "pipe:0", "-fs", str(MAX_CHAT_MEDIA_BYTES), *arguments,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Media conversion is unavailable") from exc
    try:
        output, _error = await asyncio.wait_for(
            process.communicate(content), timeout=30
        )
    except (TimeoutError, asyncio.CancelledError) as exc:
        process.kill()
        await process.communicate()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise RuntimeError("Media conversion timed out") from exc
    if process.returncode != 0 or not 0 < len(output) <= MAX_CHAT_MEDIA_BYTES:
        raise RuntimeError("Media conversion failed or exceeded the size limit")
    return output


async def prepare_media(content: bytes, mime_type: str) -> PreparedMedia:
    if mime_type == "audio/amr":
        converted = await _run_ffmpeg(
            content, "-ac", "1", "-ar", "16000", "-f", "wav", "pipe:1"
        )
        if converted[:4] != b"RIFF" or converted[8:12] != b"WAVE":
            raise RuntimeError("Converted voice has an invalid WAV header")
        return PreparedMedia(converted, "audio/wav", "customer-voice.wav")
    if mime_type == "image/gif":
        converted = await _run_ffmpeg(
            content, "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"
        )
        if not converted.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("Converted image has an invalid PNG header")
        return PreparedMedia(converted, "image/png", "customer-image.png")
    filenames = {
        "image/jpeg": "customer-image.jpg",
        "image/png": "customer-image.png",
        "image/webp": "customer-image.webp",
        "audio/wav": "customer-voice.wav",
    }
    filename = filenames.get(mime_type)
    if filename is None or not 0 < len(content) <= MAX_CHAT_MEDIA_BYTES:
        raise RuntimeError("Unsupported or oversized chat media")
    return PreparedMedia(content, mime_type, filename)


class WeComMediaBridge:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if client is None and base_url is None:
            raise ValueError("base_url is required when no client is supplied")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=60)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def upload(
        self,
        *,
        platform_api_key: str,
        visitor_id: UUID,
        media_type: str,
        mime_type: str,
        content: bytes,
    ) -> UUID:
        if media_type not in {"image", "voice"}:
            raise ValueError("Unsupported WeCom media type")
        if not mime_type.startswith("image/" if media_type == "image" else "audio/"):
            raise ValueError("WeCom media type and MIME type disagree")
        prepared = await prepare_media(content, mime_type)
        response = await self._client.post(
            "/v1/chat/upload",
            headers={"X-Platform-API-Key": platform_api_key},
            data={"channel_id": f"{visitor_id}-vtr", "channel_type": "251"},
            files={"file": (prepared.filename, prepared.content, prepared.mime_type)},
        )
        if response.status_code != 200:
            raise RuntimeError(f"Chat media upload failed with HTTP {response.status_code}")
        try:
            return UUID(response.json()["file_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Chat media upload returned an invalid file ID") from exc
