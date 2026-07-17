from __future__ import annotations

import httpx
import pytest
import zlib

from app.domain.services.media.types import MediaDownloadError
from app.domain.services.media.wecom_downloader import WeComMediaDownloader


def minimal_jpeg(*, width: int = 1, height: int = 1) -> bytes:
    start_of_frame = (
        b"\xff\xc0\x00\x0b\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x01\x01\x11\x00"
    )
    start_of_scan = b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"
    return b"\xff\xd8" + start_of_frame + start_of_scan + b"\x00\xff\xd9"


def png_chunk(name: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(name + payload).to_bytes(4, "big")
    return len(payload).to_bytes(4, "big") + name + payload + checksum


def minimal_png(*, width: int, height: int) -> bytes:
    ihdr = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + png_chunk(b"IEND", b"")
    )


def make_downloader(response: httpx.Response) -> WeComMediaDownloader:
    async def handler(_: httpx.Request) -> httpx.Response:
        return response

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WeComMediaDownloader(timeout_seconds=1, client=client)


@pytest.mark.asyncio
async def test_downloads_and_identifies_jpeg() -> None:
    downloader = make_downloader(
        httpx.Response(200, content=minimal_jpeg())
    )

    media = await downloader.download(
        access_token="secret-token",
        source_media_id="media-id",
        media_type="image",
        max_bytes=1024,
    )

    assert media.mime_type == "image/jpeg"
    assert media.extension == "jpg"
    assert len(media.sha256) == 64


@pytest.mark.asyncio
async def test_http_200_json_error_is_not_treated_as_media() -> None:
    downloader = make_downloader(
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"errcode": 40007, "errmsg": "invalid media_id"},
        )
    )

    with pytest.raises(MediaDownloadError) as error:
        await downloader.download(
            access_token="secret-token",
            source_media_id="media-id",
            media_type="image",
            max_bytes=1024,
        )

    assert error.value.code == "wecom_40007"
    assert error.value.retryable is False


@pytest.mark.asyncio
async def test_json_error_without_content_type_is_detected() -> None:
    downloader = make_downloader(
        httpx.Response(200, content=b'{"errcode":40007,"errmsg":"invalid"}')
    )

    with pytest.raises(MediaDownloadError) as error:
        await downloader.download(
            access_token="secret-token",
            source_media_id="media-id",
            media_type="image",
            max_bytes=1024,
        )

    assert error.value.code == "wecom_40007"


@pytest.mark.asyncio
async def test_oversized_media_is_permanent_failure() -> None:
    downloader = make_downloader(
        httpx.Response(200, content=b"\xff\xd8\xff12345\xff\xd9")
    )

    with pytest.raises(MediaDownloadError) as error:
        await downloader.download(
            access_token="secret-token",
            source_media_id="media-id",
            media_type="image",
            max_bytes=4,
        )

    assert error.value.code == "media_too_large"
    assert error.value.retryable is False


@pytest.mark.asyncio
async def test_rate_limit_is_retryable() -> None:
    downloader = make_downloader(httpx.Response(429))

    with pytest.raises(MediaDownloadError) as error:
        await downloader.download(
            access_token="secret-token",
            source_media_id="media-id",
            media_type="voice",
            max_bytes=1024,
        )

    assert error.value.retryable is True


@pytest.mark.asyncio
async def test_unrequested_partial_response_is_rejected() -> None:
    downloader = make_downloader(
        httpx.Response(206, content=b"\xff\xd8\xffpartial")
    )

    with pytest.raises(MediaDownloadError) as error:
        await downloader.download(
            access_token="secret-token",
            source_media_id="media-id",
            media_type="image",
            max_bytes=1024,
        )

    assert error.value.code == "unexpected_partial_response"
    assert error.value.retryable is True


@pytest.mark.asyncio
async def test_truncated_jpeg_is_rejected() -> None:
    downloader = make_downloader(httpx.Response(200, content=b"\xff\xd8\xfftruncated"))

    with pytest.raises(MediaDownloadError) as error:
        await downloader.download(
            access_token="secret-token",
            source_media_id="media-id",
            media_type="image",
            max_bytes=1024,
        )

    assert error.value.code == "unsupported_media_format"


@pytest.mark.asyncio
async def test_header_footer_only_jpeg_is_rejected() -> None:
    downloader = make_downloader(
        httpx.Response(200, content=b"\xff\xd8\xffnot-a-jpeg\xff\xd9")
    )

    with pytest.raises(MediaDownloadError) as error:
        await downloader.download(
            access_token="secret-token",
            source_media_id="media-id",
            media_type="image",
            max_bytes=1024,
        )

    assert error.value.code == "unsupported_media_format"


@pytest.mark.asyncio
async def test_image_decompression_bomb_dimensions_are_rejected() -> None:
    downloader = make_downloader(
        httpx.Response(200, content=minimal_png(width=100_000, height=100_000))
    )

    with pytest.raises(MediaDownloadError) as error:
        await downloader.download(
            access_token="secret-token",
            source_media_id="media-id",
            media_type="image",
            max_bytes=1024,
            max_image_pixels=25_000_000,
        )

    assert error.value.code == "image_dimensions_exceeded"


@pytest.mark.asyncio
async def test_header_only_amr_is_rejected() -> None:
    downloader = make_downloader(httpx.Response(200, content=b"#!AMR\n"))

    with pytest.raises(MediaDownloadError) as error:
        await downloader.download(
            access_token="secret-token",
            source_media_id="media-id",
            media_type="voice",
            max_bytes=1024,
        )

    assert error.value.code == "unsupported_media_format"
