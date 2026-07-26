"""Focused tests for Tool Store JSON-RPC execution."""

from __future__ import annotations

import uuid
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool import Tool, ToolSourceType, ToolType
from app.services.api_service import api_service_client
from app.services.tool_executor import ToolExecutor, _extract_store_output


class FakeStoreResponse:
    """Minimal HTTP response used by the Store executor tests."""

    status_code = 200
    text = ""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeStoreClient:
    """Capture the JSON-RPC request sent to the Tool Store."""

    payload: dict[str, Any] = {}
    request_json: dict[str, Any] | None = None
    request_headers: dict[str, str] | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    async def __aenter__(self) -> FakeStoreClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        del args

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
    ) -> FakeStoreResponse:
        del url
        type(self).request_json = json
        type(self).request_headers = headers
        return FakeStoreResponse(type(self).payload)


def build_express_store_tool() -> Tool:
    """Build the same Store tool shape persisted by the current project."""

    return Tool(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        name="express_service",
        title_zh="快递查询服务",
        tool_type=ToolType.MCP,
        transport_type="http",
        endpoint="https://store.example.test/express/http",
        tool_source_type=ToolSourceType.STORE,
        store_resource_id="express-resource",
        config={
            "methods": {
                "query": {
                    "params": [
                        {
                            "name": "tracking_no",
                            "type": "str",
                            "required": True,
                        },
                        {
                            "name": "company",
                            "type": "str",
                            "required": False,
                        },
                    ]
                },
                "list_companies": {"params": []},
            }
        },
    )


@pytest.mark.asyncio
async def test_store_executor_uses_mcp_tools_call_and_surfaces_jsonrpc_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeStoreClient.payload = {
        "jsonrpc": "2.0",
        "id": None,
        "error": {
            "code": -32601,
            "message": "Upstream logistics query failed",
        },
    }
    FakeStoreClient.request_json = None
    FakeStoreClient.request_headers = None
    monkeypatch.setattr(
        "app.services.tool_executor.httpx.AsyncClient",
        FakeStoreClient,
    )
    monkeypatch.setattr(
        api_service_client,
        "get_store_credential",
        AsyncMock(return_value={"api_key": "test-key"}),
    )
    executor = ToolExecutor(
        cast(AsyncSession, object()),
        uuid.uuid4(),
    )

    result = await executor._execute_store(
        build_express_store_tool(),
        {"tracking_no": "SF1234567890"},
    )

    assert FakeStoreClient.request_json == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "query",
            "arguments": {"tracking_no": "SF1234567890"},
        },
    }
    assert FakeStoreClient.request_headers == {
        "X-API-Key": "test-key",
        "Accept": "application/json, text/event-stream",
    }
    assert result.startswith("<error>")
    assert "-32601" in result
    assert "Upstream logistics query failed" in result


def test_store_executor_surfaces_mcp_tool_result_error() -> None:
    result = _extract_store_output(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "isError": True,
                "content": [
                    {
                        "type": "text",
                        "text": "Tracking number is invalid",
                    }
                ],
            },
        }
    )

    assert result == (
        "<error>Store execution failed: Tracking number is invalid</error>"
    )


@pytest.mark.asyncio
async def test_store_executor_unwraps_jsonrpc_success_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeStoreClient.payload = {
        "jsonrpc": "2.0",
        "id": None,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": '{"status":"运输中","message":"快件运输中"}',
                }
            ]
        },
    }
    FakeStoreClient.request_json = None
    monkeypatch.setattr(
        "app.services.tool_executor.httpx.AsyncClient",
        FakeStoreClient,
    )
    monkeypatch.setattr(
        api_service_client,
        "get_store_credential",
        AsyncMock(return_value={"api_key": "test-key"}),
    )
    executor = ToolExecutor(
        cast(AsyncSession, object()),
        uuid.uuid4(),
    )

    result = await executor._execute_store(
        build_express_store_tool(),
        {"tracking_no": "SF1234567890"},
    )

    assert result == '{"status":"运输中","message":"快件运输中"}'
