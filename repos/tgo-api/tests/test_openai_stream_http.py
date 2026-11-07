"""Registered compatibility HTTP routes retain current visitor authority."""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.api.v1.endpoints import chat
from tests import test_im_access as im_fixtures
from tests import test_visitor_chat_endpoints as visitor_fixtures

records = im_fixtures.records
chat_api = visitor_fixtures.chat_api


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_original_visitor_grant_can_read_the_compatibility_stream(
    chat_api, monkeypatch, failed
):
    (
        app,
        db,
        platforms,
        visitors,
        bearer,
        lookup,
        forward,
        transfer,
        profile,
    ) = chat_api
    calls = []

    async def published(**kwargs):
        calls.append(kwargs)
        if failed:
            yield {
                "event_type": "workflow_failed",
                "data": {"error_message": "synthetic-private-http-failure"},
            }
            return
        yield {
            "event_type": "agent_content_chunk",
            "data": {
                "event_type": "agent_content_chunk",
                "data": {"content_chunk": "HTTP 已核验回答"},
            },
        }
        yield {
            "event_type": "agent_response_complete",
            "data": {
                "event_type": "agent_response_complete",
                "data": {"success": True},
            },
        }
        yield {
            "event_type": "workflow_completed",
            "data": {
                "event_type": "workflow_completed",
                "data": {"success": True},
            },
        }

    monkeypatch.setattr(chat.chat_service, "is_ai_disabled", lambda *_: False)
    monkeypatch.setattr(
        chat,
        "_resolve_platform_agent_kwargs",
        AsyncMock(
            return_value={
                "agent_id": "owned-agent",
                "knowledge_channel": "web",
            }
        ),
    )
    monkeypatch.setattr(
        chat.chat_service, "process_ai_stream_to_wukongim", published
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/chat/completions",
            headers={
                "X-Platform-API-Key": platforms[0].api_key,
                "Authorization": bearer,
            },
            json={
                "messages": [{"role": "user", "content": "商品咨询"}],
                "stream": True,
            },
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.count("data: [DONE]") == 1
    assert "synthetic-private-http-failure" not in response.text
    if failed:
        assert (
            '"error"' in response.text
            and '"finish_reason":"stop"' not in response.text
        )
    else:
        assert "HTTP 已核验回答" in response.text
        assert response.text.count('"finish_reason":"stop"') == 1
    assert len(calls) == 1
    assert calls[0]["user_id"] == str(visitors[0].id)
    assert calls[0]["project_id"] == str(platforms[0].project_id)
    assert forward.await_count == 1


@pytest.mark.asyncio
async def test_streaming_flag_does_not_replace_the_visitor_grant(chat_api):
    app, _, platforms, _, _, lookup, forward, transfer, _ = chat_api
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/chat/completions",
            headers={"X-Platform-API-Key": platforms[0].api_key},
            json={
                "messages": [{"role": "user", "content": "商品咨询"}],
                "stream": True,
            },
        )
    assert response.status_code == 401
    lookup.assert_not_awaited()
    forward.assert_not_awaited()
    transfer.assert_not_awaited()
