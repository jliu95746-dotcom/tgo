"""Native transport must be usable without a Unix socket and fail honestly."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services import socket_server


class IdleServer:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def serve_forever(self):
        await asyncio.Future()

    def close(self):
        pass

    async def wait_closed(self):
        pass


@pytest.mark.asyncio
async def test_tcp_only_transport_uses_configured_loopback(monkeypatch):
    monkeypatch.setattr(socket_server.settings, "PLUGIN_SOCKET_ENABLED", False)
    monkeypatch.setattr(socket_server.settings, "PLUGIN_TCP_HOST", "127.0.0.1")
    monkeypatch.setattr(socket_server.settings, "PLUGIN_TCP_PORT", 18005)
    tcp = AsyncMock(return_value=IdleServer())
    unix = AsyncMock()
    monkeypatch.setattr(socket_server.asyncio, "start_server", tcp)
    monkeypatch.setattr(socket_server.asyncio, "start_unix_server", unix, raising=False)
    try:
        await socket_server.start_socket_server()
        assert tcp.await_args.kwargs["host"] == "127.0.0.1"
        assert tcp.await_args.kwargs["port"] == 18005
        unix.assert_not_awaited()
        assert len(socket_server._server_tasks) == 1
    finally:
        await socket_server.stop_socket_server()


@pytest.mark.asyncio
async def test_transport_bind_failure_is_not_reported_as_ready(monkeypatch):
    monkeypatch.setattr(socket_server.settings, "PLUGIN_SOCKET_ENABLED", False)
    monkeypatch.setattr(socket_server.settings, "PLUGIN_TCP_PORT", 18005)
    monkeypatch.setattr(
        socket_server.asyncio,
        "start_server",
        AsyncMock(side_effect=OSError("address in use")),
    )
    with pytest.raises(OSError, match="address in use"):
        await socket_server.start_socket_server()
    assert socket_server._server_tasks == []


@pytest.mark.asyncio
async def test_at_least_one_transport_is_required(monkeypatch):
    monkeypatch.setattr(socket_server.settings, "PLUGIN_SOCKET_ENABLED", False)
    monkeypatch.setattr(socket_server.settings, "PLUGIN_TCP_PORT", None)
    with pytest.raises(ValueError, match="transport"):
        await socket_server.start_socket_server()
