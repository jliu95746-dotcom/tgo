"""Exercise token exchange over controlled HTTP and keep failures private."""

import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import traceback
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import aiohttp
import pytest

from app.runtime.core.exceptions import MCPAuthenticationError
from app.runtime.tools.builder import agent_builder
from app.runtime.tools.builder.agent_builder import AgentBuilder
from app.runtime.tools.config import ToolsRuntimeSettings
from app.runtime.tools.models import MCPConfig
from app.runtime.tools.token import get_mcp_access_token

PRIVATE = "SYNTHETIC-TOKEN-PRIVATE"


@contextmanager
def token_server(body, status=200, delay=0):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append((self.path, self.headers, self.rfile.read(
                int(self.headers.get("Content-Length", 0)))))
            time.sleep(delay)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            content = (
                body if isinstance(body, bytes) else json.dumps(body).encode()
            )
            try:
                self.wfile.write(content)
            except OSError:
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True,
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_real_exchange_preserves_form_and_hides_token_repr():
    body = {"access_token": PRIVATE, "expires_in": 3600}
    with token_server(body) as (url, calls):
        result = await get_mcp_access_token("synthetic-subject", url + "/")
    assert result.access_token.get_secret_value() == PRIVATE
    assert PRIVATE not in repr(result)
    assert len(calls) == 1
    path, headers, body = calls[0]
    assert path == "/oauth/token"
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert parse_qs(body.decode()) == {
        "client_id": ["mcp_default"],
        "subject_token": ["synthetic-subject"],
        "grant_type": ["urn:ietf:params:oauth:grant-type:token-exchange"],
        "resource": [url + "/mcp"],
        "subject_token_type": [
            "urn:ietf:params:oauth:token-type:access_token",
        ],
    }


@pytest.mark.parametrize("body", [
    {}, None, [], "not-an-object", b"{bad-json", {"access_token": None},
    {"access_token": 12}, {"access_token": True}, {"access_token": {}},
    {"access_token": ""}, {"access_token": "  "},
    {"access_token": "value\r\n" + PRIVATE},
])
@pytest.mark.asyncio
async def test_invalid_exchange_response_is_controlled(body):
    with token_server(body) as (url, calls):
        with pytest.raises(RuntimeError) as captured:
            await get_mcp_access_token("synthetic-subject", url)
    assert PRIVATE not in "".join(traceback.format_exception(captured.value))
    assert len(calls) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500])
@pytest.mark.asyncio
async def test_http_failure_body_is_not_exposed(status):
    with token_server({"detail": PRIVATE}, status=status) as (url, calls):
        with pytest.raises(RuntimeError) as captured:
            await get_mcp_access_token("synthetic-subject", url)
    assert PRIVATE not in "".join(traceback.format_exception(captured.value))
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_timeout_is_bounded_and_private(monkeypatch):
    original = aiohttp.ClientSession

    def short_session(**kwargs):
        assert kwargs["timeout"].total is not None
        return original(timeout=aiohttp.ClientTimeout(total=0.02))

    monkeypatch.setattr(aiohttp, "ClientSession", short_session)
    with token_server({"access_token": PRIVATE}, delay=0.15) as (url, calls):
        with pytest.raises(RuntimeError) as captured:
            await get_mcp_access_token("synthetic-subject", url)
    assert PRIVATE not in "".join(traceback.format_exception(captured.value))
    assert len(calls) <= 1


def builder():
    configuration = ToolsRuntimeSettings()
    configuration.supabase.access_token = "synthetic-subject"
    return AgentBuilder(configuration)


@pytest.mark.asyncio
async def test_builder_authorization_contains_only_validated_token():
    with token_server({"access_token": PRIVATE}) as (url, calls):
        headers = await builder()._build_mcp_headers(
            MCPConfig(url=url, auth_required=True), None, None,
        )
    assert headers == {"Authorization": "Bearer " + PRIVATE}
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_builder_without_auth_makes_no_exchange(monkeypatch):
    exchange = AsyncMock()
    monkeypatch.setattr(agent_builder, "get_mcp_access_token", exchange)
    assert await builder()._build_mcp_headers(
        MCPConfig(), None, None,
    ) is None
    exchange.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_url_does_not_invent_a_default_or_make_a_call(
    monkeypatch,
):
    exchange = AsyncMock()
    monkeypatch.setattr(agent_builder, "get_mcp_access_token", exchange)
    instance = builder()
    instance._settings.mcp.default_url = "https://unused.example.test"
    with pytest.raises(MCPAuthenticationError):
        await instance._build_mcp_headers(
            MCPConfig(auth_required=True), None, None,
        )
    exchange.assert_not_awaited()


@pytest.mark.asyncio
async def test_builder_failure_does_not_expose_provider_url_or_cause(
    monkeypatch,
):
    exchange = AsyncMock(side_effect=RuntimeError(PRIVATE))
    monkeypatch.setattr(agent_builder, "get_mcp_access_token", exchange)
    with pytest.raises(MCPAuthenticationError) as captured:
        await builder()._build_mcp_headers(
            MCPConfig(
                url="https://example.test/" + PRIVATE, auth_required=True,
            ),
            PRIVATE, PRIVATE,
        )
    assert PRIVATE not in repr(captured.value.context)
    assert PRIVATE not in "".join(traceback.format_exception(captured.value))


@pytest.mark.asyncio
async def test_builder_cancellation_propagates(monkeypatch):
    exchange = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(agent_builder, "get_mcp_access_token", exchange)
    with pytest.raises(asyncio.CancelledError):
        await builder()._build_mcp_headers(
            MCPConfig(url="https://example.test", auth_required=True),
            None, None,
        )


@pytest.mark.asyncio
async def test_missing_subject_token_does_not_exchange(monkeypatch):
    exchange = AsyncMock()
    monkeypatch.setattr(agent_builder, "get_mcp_access_token", exchange)
    instance = builder()
    instance._settings.supabase.access_token = None
    with pytest.raises(MCPAuthenticationError):
        await instance._build_mcp_headers(
            MCPConfig(url="https://example.test", auth_required=True),
            None, None,
        )
    exchange.assert_not_awaited()
