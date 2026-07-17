from __future__ import annotations

import hashlib
import json
import zlib

import httpx

from app.domain.services.media.types import DownloadedMedia, MediaDownloadError


_WECOM_MEDIA_URL = "https://qyapi.weixin.qq.com/cgi-bin/media/get"


class WeComMediaDownloader:
    """Download and validate bounded temporary media from WeCom."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def download(
        self,
        *,
        access_token: str,
        source_media_id: str,
        media_type: str,
        max_bytes: int,
        max_image_pixels: int = 25_000_000,
        max_image_frames: int = 20,
        max_voice_duration_seconds: int = 300,
    ) -> DownloadedMedia:
        params = {
            "access_token": access_token,
            "media_id": source_media_id,
        }
        try:
            async with self._client.stream(
                "GET",
                _WECOM_MEDIA_URL,
                params=params,
            ) as response:
                if response.status_code == 429 or response.status_code >= 500:
                    raise MediaDownloadError(
                        f"http_{response.status_code}",
                        "WeCom media service is temporarily unavailable",
                        retryable=True,
                    )
                if response.status_code == 206:
                    raise MediaDownloadError(
                        "unexpected_partial_response",
                        "WeCom returned partial media without a Range request",
                        retryable=True,
                    )
                if response.status_code != 200:
                    raise MediaDownloadError(
                        f"http_{response.status_code}",
                        "WeCom media download was rejected",
                        retryable=response.status_code in (408, 425),
                    )

                content_type = response.headers.get("content-type", "").lower()
                if "json" in content_type:
                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(payload) + len(chunk) > 64 * 1024:
                            raise MediaDownloadError(
                                "error_payload_too_large",
                                "WeCom returned an oversized media error payload",
                                retryable=True,
                            )
                        payload.extend(chunk)
                    self._raise_api_error(bytes(payload))
                content_length = response.headers.get("content-length")
                if content_length and content_length.isdigit():
                    if int(content_length) > max_bytes:
                        raise MediaDownloadError(
                            "media_too_large",
                            f"WeCom {media_type} exceeds the configured byte limit",
                            retryable=False,
                        )

                content = bytearray()
                digest = hashlib.sha256()
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    if len(content) + len(chunk) > max_bytes:
                        raise MediaDownloadError(
                            "media_too_large",
                            f"WeCom {media_type} exceeds the configured byte limit",
                            retryable=False,
                        )
                    content.extend(chunk)
                    digest.update(chunk)
        except MediaDownloadError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise MediaDownloadError(
                "network_error",
                "WeCom media download failed due to a network error",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise MediaDownloadError(
                "http_error",
                "WeCom media download failed",
                retryable=True,
            ) from exc

        raw = bytes(content)
        if not raw:
            raise MediaDownloadError(
                "empty_media",
                "WeCom returned an empty media object",
                retryable=False,
            )
        if raw.lstrip().startswith(b"{"):
            self._raise_api_error(raw)
        mime_type, extension = _detect_media_format(
            raw,
            media_type,
            max_image_pixels=max_image_pixels,
            max_image_frames=max_image_frames,
            max_voice_duration_seconds=max_voice_duration_seconds,
        )
        return DownloadedMedia(
            content=raw,
            mime_type=mime_type,
            extension=extension,
            sha256=digest.hexdigest(),
        )

    @staticmethod
    def _raise_api_error(payload: bytes) -> None:
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MediaDownloadError(
                "invalid_error_payload",
                "WeCom returned an invalid media error payload",
                retryable=True,
            ) from exc
        errcode = int(data.get("errcode") or -1)
        retryable = errcode in {40014, 42001, 45009}
        raise MediaDownloadError(
            f"wecom_{errcode}",
            f"WeCom media API returned errcode {errcode}",
            retryable=retryable,
        )


def _detect_media_format(
    content: bytes,
    media_type: str,
    *,
    max_image_pixels: int,
    max_image_frames: int,
    max_voice_duration_seconds: int,
) -> tuple[str, str]:
    if media_type == "image":
        if _is_valid_jpeg(content, max_image_pixels=max_image_pixels):
            return "image/jpeg", "jpg"
        if _is_valid_png(content, max_image_pixels=max_image_pixels):
            return "image/png", "png"
        if _is_valid_gif(
            content,
            max_image_pixels=max_image_pixels,
            max_image_frames=max_image_frames,
        ):
            return "image/gif", "gif"
        if _is_valid_webp(content, max_image_pixels=max_image_pixels):
            return "image/webp", "webp"
    elif media_type == "voice":
        if _is_valid_amr(
            content,
            max_duration_seconds=max_voice_duration_seconds,
        ):
            return "audio/amr", "amr"
        if _is_valid_wav(
            content,
            max_duration_seconds=max_voice_duration_seconds,
        ):
            return "audio/wav", "wav"
    raise MediaDownloadError(
        "unsupported_media_format",
        f"WeCom {media_type} content did not match an allowed file signature",
        retryable=False,
    )


def _validate_image_dimensions(width: int, height: int, max_pixels: int) -> None:
    if width <= 0 or height <= 0 or width * height > max_pixels:
        raise MediaDownloadError(
            "image_dimensions_exceeded",
            "WeCom image dimensions exceed the configured pixel limit",
            retryable=False,
        )


def _is_valid_jpeg(content: bytes, *, max_image_pixels: int) -> bool:
    if not content.startswith(b"\xff\xd8") or not content.endswith(b"\xff\xd9"):
        return False
    offset = 2
    found_dimensions = False
    while offset < len(content) - 2:
        if content[offset] != 0xFF:
            return False
        while offset < len(content) and content[offset] == 0xFF:
            offset += 1
        if offset >= len(content):
            return False
        marker = content[offset]
        offset += 1
        if marker == 0xDA:
            if offset + 2 > len(content):
                return False
            segment_length = int.from_bytes(content[offset : offset + 2], "big")
            return (
                found_dimensions
                and segment_length >= 2
                and offset + segment_length <= len(content) - 2
            )
        if marker in {0x01, *range(0xD0, 0xD9)}:
            continue
        if offset + 2 > len(content):
            return False
        segment_length = int.from_bytes(content[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(content):
            return False
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            if segment_length < 8:
                return False
            height = int.from_bytes(content[offset + 3 : offset + 5], "big")
            width = int.from_bytes(content[offset + 5 : offset + 7], "big")
            _validate_image_dimensions(width, height, max_image_pixels)
            found_dimensions = True
        offset += segment_length
    return False


def _is_valid_png(content: bytes, *, max_image_pixels: int) -> bool:
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    offset = 8
    found_header = False
    found_image_data = False
    while offset + 12 <= len(content):
        chunk_length = int.from_bytes(content[offset : offset + 4], "big")
        chunk_type = content[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + chunk_length
        chunk_end = data_end + 4
        if chunk_end > len(content):
            return False
        expected_crc = int.from_bytes(content[data_end:chunk_end], "big")
        if zlib.crc32(chunk_type + content[data_start:data_end]) != expected_crc:
            return False
        if not found_header:
            if chunk_type != b"IHDR" or chunk_length != 13:
                return False
            width = int.from_bytes(content[data_start : data_start + 4], "big")
            height = int.from_bytes(content[data_start + 4 : data_start + 8], "big")
            _validate_image_dimensions(width, height, max_image_pixels)
            found_header = True
        elif chunk_type == b"IDAT":
            found_image_data = found_image_data or chunk_length > 0
        elif chunk_type == b"IEND":
            return chunk_length == 0 and found_image_data and chunk_end == len(content)
        offset = chunk_end
    return False


def _read_gif_sub_blocks(content: bytes, offset: int) -> int | None:
    while offset < len(content):
        block_size = content[offset]
        offset += 1
        if block_size == 0:
            return offset
        offset += block_size
        if offset > len(content):
            return None
    return None


def _is_valid_gif(
    content: bytes,
    *,
    max_image_pixels: int,
    max_image_frames: int,
) -> bool:
    if len(content) < 14 or not content.startswith((b"GIF87a", b"GIF89a")):
        return False
    width = int.from_bytes(content[6:8], "little")
    height = int.from_bytes(content[8:10], "little")
    _validate_image_dimensions(width, height, max_image_pixels)
    packed = content[10]
    offset = 13
    if packed & 0x80:
        offset += 3 * (2 ** ((packed & 0x07) + 1))
    frame_count = 0
    while offset < len(content):
        marker = content[offset]
        offset += 1
        if marker == 0x3B:
            return frame_count > 0 and offset == len(content)
        if marker == 0x21:
            if offset >= len(content):
                return False
            offset = _read_gif_sub_blocks(content, offset + 1) or -1
        elif marker == 0x2C:
            if offset + 9 > len(content):
                return False
            image_packed = content[offset + 8]
            offset += 9
            if image_packed & 0x80:
                offset += 3 * (2 ** ((image_packed & 0x07) + 1))
            if offset >= len(content):
                return False
            offset = _read_gif_sub_blocks(content, offset + 1) or -1
            frame_count += 1
            if frame_count > max_image_frames:
                raise MediaDownloadError(
                    "image_frame_limit_exceeded",
                    "WeCom image exceeds the configured frame limit",
                    retryable=False,
                )
        else:
            return False
        if offset < 0:
            return False
    return False


def _is_valid_webp(content: bytes, *, max_image_pixels: int) -> bool:
    if (
        len(content) < 20
        or not content.startswith(b"RIFF")
        or content[8:12] != b"WEBP"
        or int.from_bytes(content[4:8], "little") + 8 != len(content)
    ):
        return False
    offset = 12
    while offset + 8 <= len(content):
        chunk_type = content[offset : offset + 4]
        chunk_size = int.from_bytes(content[offset + 4 : offset + 8], "little")
        data_start = offset + 8
        data_end = data_start + chunk_size
        if data_end > len(content):
            return False
        payload = content[data_start:data_end]
        if chunk_type == b"VP8X" and len(payload) >= 10:
            width = 1 + int.from_bytes(payload[4:7], "little")
            height = 1 + int.from_bytes(payload[7:10], "little")
            _validate_image_dimensions(width, height, max_image_pixels)
            return True
        if (
            chunk_type == b"VP8 "
            and len(payload) >= 10
            and payload[3:6] == b"\x9d\x01\x2a"
        ):
            width = int.from_bytes(payload[6:8], "little") & 0x3FFF
            height = int.from_bytes(payload[8:10], "little") & 0x3FFF
            _validate_image_dimensions(width, height, max_image_pixels)
            return True
        if chunk_type == b"VP8L" and len(payload) >= 5 and payload[0] == 0x2F:
            bits = int.from_bytes(payload[1:5], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
            _validate_image_dimensions(width, height, max_image_pixels)
            return True
        offset = data_end + (chunk_size % 2)
    return False


def _is_valid_amr(content: bytes, *, max_duration_seconds: int) -> bool:
    frame_sizes: tuple[int, ...]
    if content.startswith(b"#!AMR\n"):
        offset = 6
        frame_sizes = (13, 14, 16, 18, 20, 21, 27, 32, 6)
    elif content.startswith(b"#!AMR-WB\n"):
        offset = 9
        frame_sizes = (18, 24, 33, 37, 41, 47, 51, 59, 61, 6)
    else:
        return False
    frame_count = 0
    while offset < len(content):
        frame_type = (content[offset] >> 3) & 0x0F
        if frame_type >= len(frame_sizes):
            return False
        offset += frame_sizes[frame_type]
        if offset > len(content):
            return False
        frame_count += 1
        if frame_count * 20 > max_duration_seconds * 1000:
            raise MediaDownloadError(
                "voice_duration_exceeded",
                "WeCom voice exceeds the configured duration limit",
                retryable=False,
            )
    return frame_count > 0 and offset == len(content)


def _is_valid_wav(content: bytes, *, max_duration_seconds: int) -> bool:
    if (
        len(content) < 44
        or not content.startswith(b"RIFF")
        or content[8:12] != b"WAVE"
        or int.from_bytes(content[4:8], "little") + 8 != len(content)
    ):
        return False
    offset = 12
    byte_rate = 0
    data_size = 0
    while offset + 8 <= len(content):
        chunk_type = content[offset : offset + 4]
        chunk_size = int.from_bytes(content[offset + 4 : offset + 8], "little")
        data_start = offset + 8
        data_end = data_start + chunk_size
        if data_end > len(content):
            return False
        if chunk_type == b"fmt " and chunk_size >= 16:
            byte_rate = int.from_bytes(
                content[data_start + 8 : data_start + 12], "little"
            )
        elif chunk_type == b"data":
            data_size += chunk_size
        offset = data_end + (chunk_size % 2)
    if byte_rate <= 0 or data_size <= 0 or offset != len(content):
        return False
    if data_size > byte_rate * max_duration_seconds:
        raise MediaDownloadError(
            "voice_duration_exceeded",
            "WeCom voice exceeds the configured duration limit",
            retryable=False,
        )
    return True
