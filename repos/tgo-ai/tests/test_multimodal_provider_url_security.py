"""Media credentials never travel to a non-loopback HTTP endpoint."""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from app.runtime.multimodal.providers.base import ProviderExecutionError
from app.runtime.multimodal.providers.http_provider import (
    ConfiguredMediaModel,
    HTTPMediaProvider,
    validate_media_content,
)
from app.schemas.multimodal import MediaAnalysisRequest, MediaType, VLMRequest
from app.schemas.multimodal import AnalysisCapability
from app.services.media_model_probe import probe_media_model

IMAGE = b"\x89PNG\r\n\x1a\nowned-image"


def _request() -> MediaAnalysisRequest:
    return MediaAnalysisRequest(
        media_id="owned-image",
        media_type=MediaType.IMAGE,
        media_uri="tgo-media://owned-test/image",
        mime_type="image/png",
        sha256=hashlib.sha256(IMAGE).hexdigest(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    (
        "http://provider.example/v1",
        "http://private.provider/v1",
        "http://localhost.evil.test/v1",
    ),
)
async def test_remote_plaintext_endpoint_is_rejected_before_key_or_media_sent(
    base_url: str,
) -> None:
    calls: list[httpx.Request] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    media_request = _request()
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = HTTPMediaProvider(
            ConfiguredMediaModel(
                name="test",
                model="vision-model",
                api_base_url=base_url,
                api_key="fixture-private-key",
            ),
            validate_media_content(media_request, IMAGE),
            client,
        )
        with pytest.raises(ProviderExecutionError) as error:
            await provider.describe(
                VLMRequest(
                    media_id=media_request.media_id,
                    media_uri=media_request.media_uri,
                    mime_type=media_request.mime_type,
                    sha256=media_request.sha256,
                )
            )
    assert error.value.diagnostic_code == "insecure_endpoint"
    assert error.value.retryable is False
    assert "fixture-private-key" not in str(error.value)
    assert calls == []


@pytest.mark.asyncio
async def test_loopback_http_remains_usable_for_local_provider_testing() -> None:
    calls: list[httpx.Request] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "一只蓝色手袋"},
                    }
                ]
            },
        )

    media_request = _request()
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = HTTPMediaProvider(
            ConfiguredMediaModel(
                name="local",
                model="vision-model",
                api_base_url="http://127.0.0.1:9999/v1",
                api_key="fixture-private-key",
            ),
            validate_media_content(media_request, IMAGE),
            client,
        )
        result = await provider.describe(
            VLMRequest(
                media_id=media_request.media_id,
                media_uri=media_request.media_uri,
                mime_type=media_request.mime_type,
                sha256=media_request.sha256,
            )
        )
    assert result.summary == "一只蓝色手袋"
    assert len(calls) == 1
    assert calls[0].url.path == "/v1/chat/completions"


@pytest.mark.asyncio
async def test_model_probe_returns_actionable_error_without_sending_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[httpx.Request] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    lookup = AsyncMock(
        return_value=ConfiguredMediaModel(
            name="remote",
            model="vision-model",
            api_base_url="http://provider.example/v1",
            api_key="fixture-private-key",
        )
    )
    monkeypatch.setattr(
        "app.services.media_model_probe.ConfiguredMultimodalService._model",
        lookup,
    )
    result = await probe_media_model(
        MagicMock(),
        uuid4(),
        uuid4(),
        "vision-model",
        AnalysisCapability.VLM,
        IMAGE,
        "image/png",
        transport=httpx.MockTransport(transport),
    )
    assert result.success is False
    assert result.error_code == "insecure_endpoint"
    assert "HTTPS" in result.message
    assert "fixture-private-key" not in result.model_dump_json()
    assert calls == []
