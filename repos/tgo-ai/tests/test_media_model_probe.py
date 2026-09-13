"""Saved-model probes are isolated, typed and use the real media adapter."""
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from app.runtime.multimodal.providers.http_provider import ConfiguredMediaModel
from app.schemas.multimodal import AnalysisCapability
from app.services.media_model_probe import probe_media_model
from tests.test_multimodal_http_provider import IMAGE


@pytest.mark.asyncio
async def test_probe_uses_selected_model_without_saving_defaults(monkeypatch):
    model = ConfiguredMediaModel(
        "owned", "qwen3.5-ocr", "https://example.test/v1", "test-secret"
    )
    lookup = AsyncMock(return_value=model)
    monkeypatch.setattr(
        "app.services.media_model_probe.ConfiguredMultimodalService._model", lookup
    )
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"text":"A1001"}'},
                    }
                ]
            },
        )

    db = MagicMock()
    project, provider = uuid4(), uuid4()
    result = await probe_media_model(
        db,
        project,
        provider,
        model.model,
        AnalysisCapability.OCR,
        IMAGE,
        "image/png",
        transport=httpx.MockTransport(respond),
    )
    assert result.success and result.output == "A1001"
    assert len(calls) == 1
    assert lookup.call_args.args[0] == project
    assert lookup.call_args.args[2].default_ocr_provider_id == provider
    db.commit.assert_not_called()
    db.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code",
    [(401, "credentials"), (403, "permission"), (404, "model"), (429, "rate_limit")],
)
async def test_probe_returns_safe_actionable_error(monkeypatch, status, code):
    lookup = AsyncMock(
        return_value=ConfiguredMediaModel(
            "owned", "qwen3.5-ocr", "https://example.test/v1", "test-secret"
        )
    )
    monkeypatch.setattr(
        "app.services.media_model_probe.ConfiguredMultimodalService._model", lookup
    )
    result = await probe_media_model(
        MagicMock(),
        uuid4(),
        uuid4(),
        "qwen3.5-ocr",
        AnalysisCapability.OCR,
        IMAGE,
        "image/png",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(status, text="test-secret")
        ),
    )
    assert not result.success and result.error_code == code
    assert "test-secret" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_probe_missing_foreign_or_inactive_model_does_not_call_provider(
    monkeypatch,
):
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.services.media_model_probe.ConfiguredMultimodalService._model", lookup
    )
    result = await probe_media_model(
        MagicMock(),
        uuid4(),
        uuid4(),
        "foreign",
        AnalysisCapability.OCR,
        IMAGE,
        "image/png",
    )
    assert not result.success and result.error_code == "configuration"


@pytest.mark.asyncio
async def test_probe_bad_bytes_rejected_before_model_lookup(monkeypatch):
    lookup = AsyncMock()
    monkeypatch.setattr(
        "app.services.media_model_probe.ConfiguredMultimodalService._model", lookup
    )
    result = await probe_media_model(
        MagicMock(),
        uuid4(),
        uuid4(),
        "image",
        AnalysisCapability.OCR,
        b"not-image",
        "image/png",
    )
    assert not result.success and result.error_code == "invalid_media"
    lookup.assert_not_called()
