"""Multipart requests keep internal authentication and strict result contracts."""

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.services.ai_client import AIServiceClient


@pytest.mark.asyncio
async def test_media_call_forwards_bytes_and_project_header(monkeypatch):
    client = AIServiceClient()
    request = AsyncMock(return_value=httpx.Response(200, json={"media_id": "file"}))
    monkeypatch.setattr(client, "_make_request", request)
    project = str(uuid4())
    result = await client.analyze_media(
        project_id=project,
        metadata={"media_id": "file"},
        content=b"image",
        mime_type="image/png",
    )
    assert result == {"media_id": "file"}
    kwargs = request.await_args.kwargs
    assert kwargs["extra_headers"]["X-Project-Id"] == project
    assert kwargs["extra_headers"]["X-Internal-API-Key"]
    assert kwargs["files"]["file"][1] == b"image"
    assert '"media_id": "file"' in kwargs["form_data"]["metadata"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="private body"),
        httpx.Response(200, json=["bad"]),
        httpx.Response(200, text="not json"),
    ],
)
async def test_media_call_never_exposes_invalid_upstream_body(monkeypatch, response):
    client = AIServiceClient()
    monkeypatch.setattr(client, "_make_request", AsyncMock(return_value=response))
    with pytest.raises(HTTPException) as error:
        await client.analyze_media(
            project_id=str(uuid4()),
            metadata={},
            content=b"image",
            mime_type="image/png",
        )
    assert error.value.status_code == 502
    assert "private body" not in str(error.value.detail)
