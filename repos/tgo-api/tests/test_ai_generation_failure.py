"""Provider error text cannot become a successful, billable customer draft."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services.ai_client import AIServiceClient


@pytest.mark.asyncio
async def test_failed_run_is_rejected_before_fallback_text(monkeypatch):
    client = AIServiceClient()
    monkeypatch.setattr(client, "_make_request", AsyncMock())
    monkeypatch.setattr(
        client,
        "_handle_response",
        AsyncMock(
            return_value={
                "success": False,
                "message": "synthetic internal failure",
                "content": "partial answer",
            }
        ),
    )
    with pytest.raises(HTTPException) as error:
        await client.run_supervisor_agent("synthetic question", "synthetic project")
    assert error.value.status_code == 502
    assert "synthetic internal failure" not in str(error.value.detail)


@pytest.mark.asyncio
async def test_successful_run_keeps_its_content(monkeypatch):
    client = AIServiceClient()
    payload = {"success": True, "content": "可公开的答复"}
    monkeypatch.setattr(client, "_make_request", AsyncMock())
    monkeypatch.setattr(client, "_handle_response", AsyncMock(return_value=payload))
    assert await client.run_supervisor_agent("question", "project") == payload
