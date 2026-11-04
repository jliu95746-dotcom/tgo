from unittest.mock import AsyncMock, patch
import httpx
import pytest
from app.engine.context import ExecutionContext
from app.engine.nodes.api import APINodeExecutor


@pytest.mark.asyncio
async def test_http_failure_is_not_a_successful_business_result():
    query = AsyncMock(return_value=httpx.Response(
        503, request=httpx.Request("GET", "https://example.test/query?order_no=PRIVATE-ORDER"),
    ))
    node = APINodeExecutor("query", {"data": {"url": "https://example.test/query"}})
    with patch("app.engine.nodes.api.bounded_api_get", query):
        with pytest.raises(RuntimeError, match="HTTP 503") as error:
            await node.execute(ExecutionContext())
    assert "PRIVATE-ORDER" not in str(error.value)


@pytest.mark.asyncio
async def test_get_with_body_never_sends_request():
    query = AsyncMock()
    node = APINodeExecutor("query", {"data": {"url": "https://example.test/query", "body_type": "json", "body": "invalid"}})
    with patch("app.engine.nodes.api.bounded_api_get", query):
        with pytest.raises(PermissionError, match="GET"):
            await node.execute(ExecutionContext())
    query.assert_not_called()


@pytest.mark.asyncio
async def test_success_resolves_actual_order_number():
    query = AsyncMock(return_value=httpx.Response(200, json={"status": "in_transit"}, request=httpx.Request("GET", "https://example.test/query")))
    node = APINodeExecutor("query", {"data": {"url": "https://example.test/query", "params": [{"key": "order_number", "value": "{{input.order_number}}"}]}})
    with patch("app.engine.nodes.api.bounded_api_get", query):
        result, _ = await node.execute(ExecutionContext({"input.order_number": "LY-123"}))
    assert query.await_args.args[2] == {"order_number": "LY-123"}
    assert result["body"]["status"] == "in_transit"


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "http://example.test/query",
    "https://127.0.0.1/query",
    "https://169.254.169.254/latest/meta-data/",
    "https://[::1]/query",
    "https://{{input.host}}/query",
    "https://user:password@example.test/query",
    "https://example.test:8443/query",
    "https://example.test\\@127.0.0.1/query",
])
async def test_unsafe_target_is_rejected_before_http_request(url):
    query = AsyncMock()
    node = APINodeExecutor("query", {"data": {"url": url}})
    with patch("app.engine.nodes.api.bounded_api_get", query):
        with pytest.raises(PermissionError, match="HTTPS|credentials"):
            await node.execute(ExecutionContext({"input.host": "127.0.0.1"}))
    query.assert_not_called()


@pytest.mark.asyncio
async def test_transport_rejection_is_not_exposed_as_successful_result():
    query = AsyncMock(side_effect=PermissionError("API 查询目标或响应无法安全核验"))
    node = APINodeExecutor("query", {"data": {"url": "https://example.test/query"}})
    with patch("app.engine.nodes.api.bounded_api_get", query):
        with pytest.raises(PermissionError, match="安全核验"):
            await node.execute(ExecutionContext())
