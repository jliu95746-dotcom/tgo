"""Startup and tool-sync diagnostics must not expose upstream contents."""

import asyncio
import logging
import traceback
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import httpx
import pytest

from app import main
from app.core import database
from app.services import process_manager, socket_server, tool_sync
from app.services.plugin_manager import PluginCapability, PluginConnection

PRIVATE = "synthetic-startup-private-350"


def assert_private(records):
    assert PRIVATE not in "\n".join(str(r.__dict__) for r in records)
    assert all(r.exc_info is None for r in records)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["socket", "monitor", "database"])
async def test_startup_failure_never_prints_raw_exception(
    monkeypatch,
    capsys,
    caplog,
    kind,
):
    caplog.set_level(logging.DEBUG)
    error = RuntimeError(PRIVATE)
    monkeypatch.setattr(tool_sync, "setup_tool_sync", Mock())
    monkeypatch.setattr(
        socket_server,
        "start_socket_server",
        AsyncMock(side_effect=error if kind == "socket" else None),
    )
    monkeypatch.setattr(
        process_manager.process_manager,
        "start",
        AsyncMock(side_effect=error if kind == "monitor" else None),
    )
    monkeypatch.setattr(database, "SessionLocal", Mock(side_effect=error))
    monkeypatch.setattr(socket_server, "stop_socket_server", AsyncMock())
    monkeypatch.setattr(
        process_manager.process_manager,
        "stop",
        AsyncMock(),
    )
    monkeypatch.setattr(
        socket_server.plugin_manager,
        "shutdown_all",
        AsyncMock(),
    )
    if kind == "database":
        async with main.lifespan(main.app):
            pass
    else:
        with pytest.raises(RuntimeError) as raised:
            async with main.lifespan(main.app):
                pytest.fail("startup must fail")
        assert PRIVATE not in "".join(traceback.format_exception(raised.value))
    assert PRIVATE not in capsys.readouterr().out
    assert_private(caplog.records)


@pytest.mark.asyncio
async def test_transport_bind_error_is_not_logged_with_traceback(
    monkeypatch,
    caplog,
):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(socket_server.settings, "PLUGIN_SOCKET_ENABLED", False)
    monkeypatch.setattr(socket_server.settings, "PLUGIN_TCP_PORT", 18005)
    monkeypatch.setattr(
        socket_server.asyncio,
        "start_server",
        AsyncMock(side_effect=OSError(PRIVATE)),
    )
    with pytest.raises(OSError):
        await socket_server.start_socket_server()
    assert socket_server._server_tasks == []
    assert_private(caplog.records)


@pytest.mark.asyncio
async def test_failed_startup_cleanup_is_private_and_never_marks_ready(
    monkeypatch,
    caplog,
    capsys,
):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(tool_sync, "setup_tool_sync", Mock())
    monkeypatch.setattr(
        socket_server,
        "start_socket_server",
        AsyncMock(side_effect=RuntimeError(PRIVATE)),
    )
    stop_transport = AsyncMock(side_effect=RuntimeError(PRIVATE))
    stop_monitor = AsyncMock(side_effect=RuntimeError(PRIVATE))
    monkeypatch.setattr(socket_server, "stop_socket_server", stop_transport)
    monkeypatch.setattr(process_manager.process_manager, "stop", stop_monitor)
    with pytest.raises(RuntimeError) as raised:
        async with main.lifespan(main.app):
            pytest.fail("startup must fail")
    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None
    assert PRIVATE not in "".join(traceback.format_exception(raised.value))
    output = capsys.readouterr().out
    assert PRIVATE not in output and "is ready" not in output
    stop_transport.assert_awaited_once()
    stop_monitor.assert_awaited_once()
    assert_private(caplog.records)


@pytest.mark.asyncio
async def test_socket_path_failures_keep_cleanup_and_hide_path(
    monkeypatch,
    caplog,
    tmp_path,
):
    caplog.set_level(logging.DEBUG)
    path = tmp_path / PRIVATE
    path.touch()
    monkeypatch.setattr(socket_server.settings, "PLUGIN_SOCKET_ENABLED", True)
    monkeypatch.setattr(
        socket_server.settings, "PLUGIN_SOCKET_PATH", str(path)
    )
    monkeypatch.setattr(socket_server.settings, "PLUGIN_TCP_PORT", None)
    monkeypatch.setattr(
        socket_server.os, "unlink", Mock(side_effect=OSError(PRIVATE))
    )
    monkeypatch.setattr(
        socket_server.os, "chmod", Mock(side_effect=OSError(PRIVATE))
    )
    server = MagicMock()
    server.__aenter__.return_value = server
    server.serve_forever = AsyncMock()
    server.wait_closed = AsyncMock()
    monkeypatch.setattr(
        socket_server.asyncio,
        "start_unix_server",
        AsyncMock(return_value=server),
        raising=False,
    )
    try:
        await socket_server.start_socket_server()
        assert len(socket_server._server_tasks) == 1
    finally:
        await socket_server.stop_socket_server()
    server.close.assert_called_once()
    assert socket_server._server_tasks == []
    assert_private(caplog.records)


@pytest.mark.asyncio
async def test_successful_lifespan_keeps_order_and_installed_configuration(
    monkeypatch,
    caplog,
    capsys,
):
    caplog.set_level(logging.DEBUG)
    events = []

    def operation(name):
        async def invoke():
            events.append(name)

        return invoke

    monkeypatch.setattr(tool_sync, "setup_tool_sync", Mock())
    monkeypatch.setattr(
        socket_server, "start_socket_server", operation("transport")
    )
    monkeypatch.setattr(
        socket_server, "stop_socket_server", operation("stop transport")
    )
    monkeypatch.setattr(
        process_manager.process_manager, "start", operation("monitor")
    )
    monkeypatch.setattr(
        process_manager.process_manager, "stop", operation("stop monitor")
    )
    monkeypatch.setattr(
        socket_server.plugin_manager,
        "shutdown_all",
        operation("shutdown"),
    )
    row = SimpleNamespace(
        plugin_id="fixture",
        project_id="owned",
        name=PRIVATE,
        version="1",
        source_config={"url": PRIVATE},
        build_config={"language": "python"},
        runtime_config={"env": {"TOKEN": PRIVATE}},
    )
    session = MagicMock()
    session.__enter__.return_value = session
    session.query.return_value.filter.return_value.all.return_value = [row]
    monkeypatch.setattr(database, "SessionLocal", Mock(return_value=session))
    start = AsyncMock()
    monkeypatch.setattr(process_manager.process_manager, "start_plugin", start)
    async with main.lifespan(main.app):
        await asyncio.sleep(0)
        assert events == ["transport", "monitor"]
        start.assert_awaited_once_with(
            "fixture",
            {
                "id": "fixture",
                "project_id": "owned",
                "name": PRIVATE,
                "version": "1",
                "source": row.source_config,
                "build": row.build_config,
                "runtime": row.runtime_config,
            },
        )
    assert events == [
        "transport",
        "monitor",
        "shutdown",
        "stop monitor",
        "stop transport",
    ]
    assert PRIVATE not in capsys.readouterr().out
    assert_private(caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["register", "delete"])
@pytest.mark.parametrize("outcome", ["ok", "status", "transport"])
async def test_tool_sync_keeps_requests_and_hides_private_diagnostics(
    monkeypatch,
    caplog,
    method,
    outcome,
):
    caplog.set_level(logging.DEBUG)
    observed = []
    status = 200 if outcome == "ok" else 502

    async def handle(request):
        observed.append(request)
        if outcome == "transport":
            raise httpx.ConnectError(PRIVATE, request=request)
        return httpx.Response(status, text=PRIVATE)

    original = httpx.AsyncClient

    def client(**kwargs):
        return original(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(tool_sync.httpx, "AsyncClient", client)
    service = tool_sync.PluginToolSyncService()
    service.ai_base_url = f"https://service.test/{PRIVATE}"
    if method == "register":
        await service._register_tool(
            {
                "name": PRIVATE,
                "config": {
                    "private": PRIVATE,
                },
            }
        )
        assert observed[0].method == "POST"
        assert (
            observed[0].content
            == (
                '{"name":"'
                + PRIVATE
                + '","config":{"private":"'
                + PRIVATE
                + '"}}'
            ).encode()
        )
        assert observed[0].url.path.endswith("/api/v1/tools/plugin")
    else:
        await service._delete_tools_by_prefix(PRIVATE)
        assert observed[0].method == "DELETE"
        assert observed[0].url.params["prefix"] == PRIVATE
        assert observed[0].url.path.endswith("/tools/plugin/by-prefix")
    assert len(observed) == 1
    assert_private(caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("project", [None, "owned"])
async def test_sync_metadata_is_not_logged_and_payload_is_preserved(
    monkeypatch,
    caplog,
    project,
):
    caplog.set_level(logging.DEBUG)
    service = tool_sync.PluginToolSyncService()
    register, delete = AsyncMock(), AsyncMock()
    monkeypatch.setattr(service, "_register_tool", register)
    monkeypatch.setattr(service, "_delete_tools_by_prefix", delete)
    plugin = PluginConnection(
        id=PRIVATE,
        name=PRIVATE,
        version="1",
        project_id=project,
        capabilities=[
            PluginCapability(
                type="mcp_tools",
                title="tools",
                tools=[
                    {
                        "name": "query",
                        "title": "Query",
                        "description": PRIVATE,
                        "parameters": [{"name": "order", "type": "string"}],
                    }
                ],
            )
        ],
    )
    await service.sync_plugin_tools(plugin)
    if project:
        register.assert_awaited_once()
        payload = register.await_args.args[0]
        assert payload["name"] == f"plugin:{PRIVATE}:query"
        assert payload["project_id"] == project
        assert payload["description"] == PRIVATE
        assert payload["config"]["parameters"][0]["name"] == "order"
    else:
        register.assert_not_awaited()
    await service.remove_plugin_tools(PRIVATE)
    delete.assert_awaited_once_with(f"plugin:{PRIVATE}:")
    assert_private(caplog.records)
