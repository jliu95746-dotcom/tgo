"""Node execution receipts must not copy credentials from configuration or HTTP headers."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.engine.context import ExecutionContext
from app.engine.executor import WorkflowExecutor
from app.engine.nodes.api import APINodeExecutor


@pytest.mark.asyncio
async def test_node_callback_does_not_receive_api_configuration_as_input():
    secret = "private-api-credential"
    definition = {
        "nodes": [
            {"id": "start", "type": "input", "data": {
                "reference_key": "input",
                "input_variables": [{"name": "order_no", "type": "string"}],
            }},
            {"id": "query", "type": "api", "data": {
                "reference_key": "query", "url": "https://example.test/query",
                "headers": [{"key": "Authorization", "value": secret}],
            }},
            {"id": "answer", "type": "answer", "data": {
                "reference_key": "answer", "output_type": "template",
                "output_template": "完成",
            }},
        ],
        "edges": [
            {"source": "start", "target": "query"},
            {"source": "query", "target": "answer"},
        ],
    }
    query = AsyncMock(return_value=httpx.Response(
        200, json={"status": "shipped"},
        request=httpx.Request("GET", "https://example.test/query"),
    ))
    complete = AsyncMock()
    with patch("app.engine.nodes.api.bounded_api_get", query):
        result = await WorkflowExecutor(definition, project_id="project-a").run(
            {"order_no": "ORDER-1"}, on_node_complete=complete,
        )
    assert result == "完成"
    assert query.await_args.args[1]["Authorization"] == secret
    assert complete.await_count == 3
    for call in complete.await_args_list:
        assert call.kwargs["input"] == {"capture_status": "redacted"}
        assert secret not in str(call.kwargs["input"])


@pytest.mark.asyncio
async def test_api_node_does_not_publish_response_headers():
    secret = "private-upstream-cookie"
    node = APINodeExecutor("query", {"data": {"url": "https://example.test/query"}})
    query = AsyncMock(return_value=httpx.Response(
        200, json={"status": "shipped"},
        headers={"set-cookie": secret, "content-type": "application/json"},
        request=httpx.Request("GET", "https://example.test/query"),
    ))
    with patch("app.engine.nodes.api.bounded_api_get", query):
        result, _ = await node.execute(ExecutionContext())
    assert result["body"] == {"status": "shipped"}
    assert result["headers"] == {}
    assert secret not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("method, body_type", [
    ("POST", "json"), ("PUT", "none"),
    ("DELETE", "none"), ("GET", "json"),
])
async def test_api_node_rejects_unreviewed_write_before_http_request(
    method, body_type,
):
    node = APINodeExecutor("query", {"data": {
        "url": "https://example.test/orders",
        "method": method, "body_type": body_type,
    }})
    query = AsyncMock()
    with patch("app.engine.nodes.api.bounded_api_get", query):
        with pytest.raises(PermissionError, match="GET"):
            await node.execute(ExecutionContext())
    query.assert_not_awaited()
