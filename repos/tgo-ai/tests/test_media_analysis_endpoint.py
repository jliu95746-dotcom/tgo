"""Authenticated multipart endpoint using the unchanged canonical metadata JSON."""

import hashlib
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.api.v1 import media_analysis
from app.config import settings
from app.dependencies import get_db
from app.main import app
from app.runtime.multimodal.providers.base import MultimodalProviderRegistry
from app.schemas.multimodal import MediaAnalysisRequest, MediaType, ProviderSelection
from app.services.multimodal_service import MultimodalService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["valid", "missing-auth", "bad-key", "external-uri", "oversized"]
)
async def test_media_route_checks_auth_metadata_and_size(monkeypatch, mode):
    content = b"\x89PNG\r\n\x1a\nowned-image"
    request = MediaAnalysisRequest(
        media_id="owned-file",
        media_type=MediaType.IMAGE,
        media_uri="tgo-media://owned/file",
        mime_type="image/png",
        sha256=hashlib.sha256(content).hexdigest(),
    )
    result = await MultimodalService(
        MultimodalProviderRegistry(), ProviderSelection()
    ).analyze(request)
    analyze = AsyncMock(return_value=result)
    monkeypatch.setattr(media_analysis.ConfiguredMultimodalService, "analyze", analyze)

    async def database():
        yield None

    before = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = database
    headers = {"X-Internal-API-Key": settings.secret_key, "X-Project-Id": str(uuid4())}
    metadata = request.model_dump(mode="json")
    if mode == "missing-auth":
        headers = {}
    if mode == "bad-key":
        headers["X-Internal-API-Key"] = "invalid-fixture-key"
    if mode == "external-uri":
        metadata["media_uri"] = "https://do-not-fetch.example/private"
    if mode == "oversized":
        monkeypatch.setattr(settings, "multimodal_max_media_bytes", 8)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/analysis/media",
                headers=headers,
                data={"metadata": json.dumps(metadata)},
                files={"file": ("image.png", content, "image/png")},
            )
        expected = {
            "valid": 200,
            "missing-auth": 401,
            "bad-key": 401,
            "external-uri": 422,
            "oversized": 413,
        }[mode]
        assert response.status_code == expected
        if mode == "valid":
            assert analyze.call_args.args[2] == content
            assert response.json()["can_continue"] is False
        else:
            analyze.assert_not_awaited()
        assert "do-not-fetch.example" not in response.text
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(before)
