"""Preserve valid external results and reject private failure bodies."""

import json
import logging
import traceback

import httpx
import pytest

from app.exceptions import (
    AuthenticationError, AuthorizationError, ExternalServiceError,
    NotFoundError, RateLimitError, ValidationError,
)
from app.schemas.agent_run import SupervisorRunRequest
from app.services.agent_runtime_service import AgentRuntimeServiceClient
from app.services.mcp_service import MCPServiceClient, MCPServiceError

PRIVATE = "SYNTHETIC-REMOTE-PRIVATE"


@pytest.fixture
def http_reply(monkeypatch):
    original = httpx.AsyncClient
    requests = []

    def configure(body=None, *, status=200, content=None, error=None):
        def handle(request):
            requests.append(request)
            if error is not None:
                raise error(PRIVATE, request=request)
            if content is not None:
                return httpx.Response(status, content=content)
            return httpx.Response(status, json=body)

        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda **kwargs: original(
                transport=httpx.MockTransport(handle), **kwargs,
            ),
        )
        return requests

    return configure


def make_client(kind):
    client = (
        MCPServiceClient() if kind == "mcp" else AgentRuntimeServiceClient()
    )
    client.base_url = "https://runtime.example.test"
    return client


async def invoke(client, kind):
    if kind == "mcp":
        return await client._make_request(
            "POST", "/v1/tools", params={"project_id": "project-1"},
            json_data={"message": "hello"}, headers={"X-Request-ID": "req-1"},
        )
    return await client.run_supervisor(
        SupervisorRunRequest(message="hello"), "synthetic-api-key",
        request_id="req-1",
    )


def assert_private(error, caplog):
    assert PRIVATE not in str(error)
    assert PRIVATE not in repr(getattr(error, "details", None))
    assert PRIVATE not in repr(getattr(error, "response_data", None))
    assert PRIVATE not in "".join(traceback.format_exception(error))
    assert PRIVATE not in caplog.text


@pytest.mark.parametrize("kind", ["mcp", "runtime"])
@pytest.mark.parametrize("status", [200, 201])
@pytest.mark.asyncio
async def test_valid_json_and_request_are_preserved(kind, status, http_reply):
    body = {"result": "中文回答", "extra": {"values": [1, True, None, "text"]}}
    requests = http_reply(body, status=status)
    assert await invoke(make_client(kind), kind) == body
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.headers["X-Request-ID"] == "req-1"
    actual = json.loads(request.content)
    if kind == "mcp":
        assert request.url.path == "/v1/tools"
        assert request.url.params["project_id"] == "project-1"
        assert actual == {"message": "hello"}
    else:
        assert request.url.path == "/run"
        assert request.headers["X-API-Key"] == "synthetic-api-key"
        assert actual == SupervisorRunRequest(message="hello").model_dump(
            mode="json", exclude_none=True,
        )


@pytest.mark.parametrize("kind", ["mcp", "runtime"])
@pytest.mark.parametrize("body", [None, [], PRIVATE, 12, True])
@pytest.mark.asyncio
async def test_non_object_success_is_a_controlled_failure(
    kind, body, http_reply, caplog,
):
    http_reply(body)
    expected = MCPServiceError if kind == "mcp" else ExternalServiceError
    with pytest.raises(expected) as captured:
        await invoke(make_client(kind), kind)
    assert_private(captured.value, caplog)


@pytest.mark.parametrize("kind", ["mcp", "runtime"])
@pytest.mark.parametrize("content", [b"", b"{bad-json", b"<html>error</html>"])
@pytest.mark.asyncio
async def test_malformed_json_is_a_controlled_failure(
    kind, content, http_reply, caplog,
):
    http_reply(content=content)
    expected = MCPServiceError if kind == "mcp" else ExternalServiceError
    with pytest.raises(expected) as captured:
        await invoke(make_client(kind), kind)
    assert_private(captured.value, caplog)


@pytest.mark.parametrize("kind", ["mcp", "runtime"])
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 429, 500, 503])
@pytest.mark.asyncio
async def test_status_mapping_keeps_upstream_body_private(
    kind, status, http_reply, caplog,
):
    caplog.set_level(logging.INFO, logger="app.services.mcp_service")
    caplog.set_level(logging.INFO, logger="app.services.agent_runtime_service")
    requests = http_reply({"detail": PRIVATE}, status=status)
    expected = MCPServiceError if kind == "mcp" else {
        400: ValidationError, 422: ValidationError,
        401: AuthenticationError, 403: AuthorizationError,
        404: NotFoundError, 429: RateLimitError,
    }.get(status, ExternalServiceError)
    with pytest.raises(expected) as captured:
        await invoke(make_client(kind), kind)
    if kind == "mcp":
        assert captured.value.status_code == status
    assert len(requests) == 1
    assert_private(captured.value, caplog)


@pytest.mark.parametrize("kind", ["mcp", "runtime"])
@pytest.mark.parametrize("error", [
    httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError,
])
@pytest.mark.asyncio
async def test_transport_failure_is_private(kind, error, http_reply, caplog):
    http_reply(error=error)
    expected = MCPServiceError if kind == "mcp" else ExternalServiceError
    with pytest.raises(expected) as captured:
        await invoke(make_client(kind), kind)
    assert_private(captured.value, caplog)


@pytest.mark.asyncio
async def test_mcp_no_content_keeps_existing_empty_result(http_reply):
    http_reply(status=204, content=b"")
    assert await invoke(make_client("mcp"), "mcp") == {}


@pytest.mark.asyncio
async def test_streaming_success_is_forwarded_unchanged(http_reply):
    text = 'data: {"delta":"中文"}\n\ndata: [DONE]\n\n'
    requests = http_reply(content=text.encode())
    stream = await make_client("runtime").stream_supervisor(
        SupervisorRunRequest(message="hello"), "synthetic-api-key",
        request_id="req-1",
    )
    assert "".join([part async for part in stream]) == text
    assert json.loads(requests[0].content)["stream"] is True


@pytest.mark.parametrize("status", [400, 404, 500])
@pytest.mark.asyncio
async def test_stream_error_uses_the_same_private_status_mapping(
    status, http_reply, caplog,
):
    http_reply({"detail": PRIVATE}, status=status)
    stream = await make_client("runtime").stream_supervisor(
        SupervisorRunRequest(message="hello"), "synthetic-api-key",
    )
    expected = {400: ValidationError, 404: NotFoundError}.get(
        status, ExternalServiceError,
    )
    with pytest.raises(expected) as captured:
        _ = [part async for part in stream]
    assert_private(captured.value, caplog)


@pytest.mark.parametrize("error", [
    httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError,
])
@pytest.mark.asyncio
async def test_stream_transport_failure_is_private(error, http_reply, caplog):
    http_reply(error=error)
    stream = await make_client("runtime").stream_supervisor(
        SupervisorRunRequest(message="hello"), "synthetic-api-key",
    )
    with pytest.raises(ExternalServiceError) as captured:
        _ = [part async for part in stream]
    assert_private(captured.value, caplog)


@pytest.mark.asyncio
async def test_partial_stream_failure_closes_response(monkeypatch, caplog):
    closed = []
    original = httpx.AsyncClient

    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: ok\n\n"
            raise httpx.ReadError(PRIVATE)

        async def aclose(self):
            closed.append(True)

    def handle(request):
        return httpx.Response(200, stream=BrokenStream())

    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: original(
            transport=httpx.MockTransport(handle), **kwargs,
        ),
    )
    stream = await make_client("runtime").stream_supervisor(
        SupervisorRunRequest(message="hello"), "synthetic-api-key",
    )
    received = []
    with pytest.raises(ExternalServiceError) as captured:
        async for part in stream:
            received.append(part)
    assert received == ["data: ok\n\n"]
    assert closed == [True]
    assert_private(captured.value, caplog)


@pytest.mark.asyncio
async def test_unexpected_mcp_client_failure_is_private(monkeypatch, caplog):
    def fail(**kwargs):
        raise RuntimeError(PRIVATE)

    monkeypatch.setattr(httpx, "AsyncClient", fail)
    with pytest.raises(MCPServiceError) as captured:
        await invoke(make_client("mcp"), "mcp")
    assert captured.value.status_code == 500
    assert_private(captured.value, caplog)
