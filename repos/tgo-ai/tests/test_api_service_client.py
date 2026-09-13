from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.services.api_service import APIServiceClient


@pytest.mark.asyncio
async def test_store_credential_is_cached_between_agent_builds() -> None:
    """Repeated chat turns should not refetch the same local credential."""

    response = httpx.Response(200, json={"api_key": "masked-test-key"})
    get = AsyncMock(return_value=response)
    client = APIServiceClient()
    client._http_client = Mock(get=get, is_closed=False)

    first = await client.get_store_credential("project-1")
    second = await client.get_store_credential("project-1")

    assert first == second
    assert first == {"api_key": "masked-test-key"}
    assert get.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"[]", b"null", b'"invalid"', b"not-json"])
async def test_invalid_store_response_is_not_cached(body: bytes) -> None:
    response = httpx.Response(200, content=body)
    get = AsyncMock(return_value=response)
    client = APIServiceClient()
    client._http_client = Mock(get=get, is_closed=False)

    assert await client.get_store_credential("project-1") is None
    assert await client.get_store_credential("project-1") is None
    assert get.await_count == 2
    assert not client._credential_cache


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [b'{"success":true,"content":"ok"}', b"[]", b"null", b"not-json"]
)
async def test_plugin_response_must_be_a_json_object(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
) -> None:
    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.post.return_value = httpx.Response(200, content=body)
    monkeypatch.setattr(
        "app.services.api_service.httpx.AsyncClient", Mock(return_value=fake_client)
    )
    client = APIServiceClient()

    result = await client.execute_plugin_tool("fixture", "query", {}, {}, project_id="owned")
    if body.startswith(b"{"):
        assert result == {"success": True, "content": "ok"}
    else:
        assert result["success"] is False
        assert result["error"] == "Invalid plugin response format"
        assert result["content"] == "工具返回的数据格式不正确"
