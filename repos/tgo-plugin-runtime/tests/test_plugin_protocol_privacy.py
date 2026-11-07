"""Protocol diagnostics must not publish plugin data or exception contents."""

import asyncio
import json
import logging
import struct
import traceback
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from jose import jwt
import pytest

from app.services import plugin_manager as registry
from app.services import socket_server
from app.services.plugin_failures import public_registration_failure

PRIVATE = "synthetic-protocol-private-350"
TEST_KEY = "synthetic-signing-key-for-protocol-tests-350"


def frame(message):
    data = json.dumps(message, ensure_ascii=False).encode()
    return struct.pack(">I", len(data)) + data


def reader_for(*messages):
    reader = asyncio.StreamReader()
    for message in messages:
        reader.feed_data(frame(message))
    reader.feed_eof()
    return reader


def writer_for(peer=PRIVATE):
    writer = Mock()
    writer.get_extra_info.return_value = peer
    writer.is_closing.return_value = False
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    return writer


def assert_private(records):
    assert PRIVATE not in "\n".join(str(r.__dict__) for r in records)
    assert all(r.exc_info is None for r in records)


@pytest.fixture
def manager(monkeypatch):
    instance = registry.plugin_manager
    monkeypatch.setattr(instance, "_plugins", {})
    monkeypatch.setattr(instance, "_tool_sync", None)
    return instance


@pytest.mark.asyncio
async def test_receive_failure_never_logs_exception_contents(caplog):
    caplog.set_level(logging.DEBUG)
    reader = SimpleNamespace(
        readexactly=AsyncMock(side_effect=OSError(PRIVATE))
    )
    assert await socket_server._recv_message(reader) is None
    assert_private(caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason,expected",
    [
        (PRIVATE, "Plugin registration failed"),
        ("Invalid dev_token type", "Invalid dev_token type"),
        ("Invalid dev_token", "Invalid dev_token"),
        (
            "Debug connection requires dev_token",
            "Debug connection requires dev_token",
        ),
    ],
)
async def test_registration_errors_are_controlled(
    monkeypatch,
    caplog,
    reason,
    expected,
):
    caplog.set_level(logging.DEBUG)
    register = AsyncMock(side_effect=ValueError(reason))
    monkeypatch.setattr(socket_server.plugin_manager, "register", register)
    writer = writer_for()
    reader = reader_for(
        {
            "id": 7,
            "method": "register",
            "params": {"name": PRIVATE, "dev_token": PRIVATE},
        }
    )
    await socket_server._handle_client(reader, writer)
    data = writer.write.call_args.args[0]
    assert struct.unpack(">I", data[:4])[0] == len(data[4:])
    assert json.loads(data[4:]) == {
        "jsonrpc": "2.0",
        "id": 7,
        "error": {"code": -32000, "message": expected},
    }
    writer.close.assert_called_once()
    writer.wait_closed.assert_awaited_once()
    assert_private(caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["first", "timeout", "unexpected", "read"])
async def test_connection_logs_exclude_peer_and_untrusted_metadata(
    monkeypatch,
    caplog,
    kind,
):
    caplog.set_level(logging.DEBUG)
    plugin = registry.PluginConnection(
        id=PRIVATE, name=PRIVATE, version=PRIVATE
    )
    register = AsyncMock(return_value=(PRIVATE, plugin))
    unregister = AsyncMock()
    monkeypatch.setattr(socket_server.plugin_manager, "register", register)
    monkeypatch.setattr(socket_server.plugin_manager, "unregister", unregister)
    messages = [
        {
            "id": 1,
            "method": "register",
            "params": {
                "name": PRIVATE,
                "version": PRIVATE,
            },
        }
    ]
    if kind == "first":
        messages = [{"id": 1, "method": PRIVATE}]
    elif kind == "unexpected":
        messages.append({"method": PRIVATE})
    elif kind in {"timeout", "read"}:
        error = (
            asyncio.TimeoutError(PRIVATE)
            if kind == "timeout"
            else (RuntimeError(PRIVATE))
        )
        monkeypatch.setattr(
            socket_server,
            "_recv_message",
            AsyncMock(side_effect=error),
        )
    writer = writer_for()
    await socket_server._handle_client(reader_for(*messages), writer)
    assert_private(caplog.records)
    writer.close.assert_called_once()
    if kind == "unexpected":
        unregister.assert_awaited_once_with(
            PRIVATE,
            expected_connection=plugin,
        )


@pytest.mark.asyncio
async def test_token_decode_error_has_no_original_text_or_chain(
    monkeypatch,
    caplog,
    manager,
):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(
        registry.jwt,
        "decode",
        Mock(side_effect=registry.JWTError(PRIVATE)),
    )
    with pytest.raises(ValueError) as raised:
        await manager.register(
            PRIVATE,
            "1",
            [],
            reader_for(),
            writer_for(),
            dev_token=PRIVATE,
        )
    assert str(raised.value) == "Invalid dev_token"
    assert PRIVATE not in "".join(traceback.format_exception(raised.value))
    assert_private(caplog.records)
    assert not manager.plugins


@pytest.mark.parametrize(
    "error",
    [
        ValueError(),
        ValueError({"key": PRIVATE}),
        ValueError("Invalid dev_token " + PRIVATE),
    ],
)
def test_registration_reason_requires_an_exact_declared_string(error):
    assert public_registration_failure(error) == "Plugin registration failed"


@pytest.mark.asyncio
async def test_actual_capability_validation_is_private(
    monkeypatch,
    caplog,
    manager,
):
    caplog.set_level(logging.DEBUG)
    writer = writer_for()
    await socket_server._handle_client(
        reader_for(
            {
                "id": 8,
                "method": "register",
                "params": {
                    "name": "fixture",
                    "capabilities": [{"type": PRIVATE}],
                },
            }
        ),
        writer,
    )
    response = json.loads(writer.write.call_args.args[0][4:])
    assert response["error"] == {
        "code": -32000,
        "message": "Plugin registration failed",
    }
    assert not manager.plugins
    assert_private(caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_table", [True, False])
async def test_database_lookup_failure_keeps_tcp_registration_closed(
    monkeypatch,
    caplog,
    manager,
    missing_table,
):
    from app.core import database

    caplog.set_level(logging.DEBUG)
    message = PRIVATE + (" pg_installed_plugins" if missing_table else "")
    session = AsyncMock()
    session.execute.side_effect = RuntimeError(message)
    context = AsyncMock()
    context.__aenter__.return_value = session
    monkeypatch.setattr(
        database, "AsyncSessionLocal", Mock(return_value=context)
    )
    with pytest.raises(
        ValueError, match="Debug connection requires dev_token"
    ):
        await manager.register(
            PRIVATE,
            "1",
            [],
            reader_for(),
            writer_for(),
            plugin_id=PRIVATE,
            is_tcp=True,
        )
    assert not manager.plugins
    assert_private(caplog.records)


@pytest.mark.asyncio
async def test_cancelled_request_preserves_cancellation_and_cleans_future(
    monkeypatch,
    manager,
):
    plugin = registry.PluginConnection(
        id="fixture",
        name="fixture",
        version="1",
        writer=writer_for(),
    )
    manager.plugins[plugin.id] = plugin
    started = asyncio.Event()

    async def send(writer, request):
        started.set()
        await asyncio.Future()

    monkeypatch.setattr(manager, "_send_message", send)
    pending = asyncio.create_task(manager.send_request("fixture", "query", {}))
    try:
        await asyncio.wait_for(started.wait(), 2)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not plugin._pending_requests
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_registry_and_scope_diagnostics_do_not_echo_registration(
    monkeypatch,
    caplog,
    manager,
):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(registry.settings, "SECRET_KEY", TEST_KEY)
    token = jwt.encode(
        {
            "type": "plugin_dev",
            "project_id": "owned",
            "user_id": "staff",
        },
        TEST_KEY,
        algorithm="HS256",
    )
    plugin_id, plugin = await manager.register(
        PRIVATE,
        PRIVATE,
        [],
        reader_for(),
        writer_for(),
        plugin_id=PRIVATE,
        dev_token=token,
        is_tcp=True,
    )
    assert plugin_id == PRIVATE and plugin.project_id == "owned"
    assert plugin.name == PRIVATE
    assert manager.get_plugin(PRIVATE, project_id="foreign") is None
    await manager.unregister(PRIVATE, expected_connection=plugin)
    assert not manager.plugins
    assert_private(caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["error", "send", "timeout", "missing"])
async def test_request_failures_keep_no_private_diagnostics(
    monkeypatch,
    caplog,
    manager,
    kind,
):
    caplog.set_level(logging.DEBUG)
    plugin = registry.PluginConnection(
        id=PRIVATE,
        name=PRIVATE,
        version="1",
        project_id="owned",
        writer=writer_for(),
    )
    if kind != "missing":
        manager.plugins[PRIVATE] = plugin

    async def send(writer, request):
        if kind == "send":
            raise OSError(PRIVATE)
        if kind == "error":
            await manager.handle_response(
                plugin,
                {
                    "id": request["id"],
                    "error": {"code": -1, "message": PRIVATE, "data": PRIVATE},
                },
            )

    monkeypatch.setattr(manager, "_send_message", send)
    assert (
        await manager.send_request(
            PRIVATE,
            PRIVATE,
            {"private": PRIVATE},
            timeout=0.01,
            project_id="owned",
        )
        is None
    )
    assert not plugin._pending_requests
    assert_private(caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["validation", "request"])
async def test_panel_errors_do_not_expose_customer_contents(
    monkeypatch,
    caplog,
    manager,
    kind,
):
    caplog.set_level(logging.DEBUG)
    plugin = registry.PluginConnection(
        id=PRIVATE,
        name=PRIVATE,
        version="1",
        capabilities=[
            registry.PluginCapability(
                type="visitor_panel",
                title="panel",
            )
        ],
    )
    manager.plugins[PRIVATE] = plugin
    send = AsyncMock(
        side_effect=RuntimeError(PRIVATE) if kind == "request" else None,
        return_value={"type": "invalid", "version": PRIVATE, "ui": PRIVATE},
    )
    monkeypatch.setattr(manager, "send_request", send)
    assert (
        await manager.render_visitor_panels(
            "visitor",
            None,
            None,
            {"private": PRIVATE},
        )
        == []
    )
    assert_private(caplog.records)


@pytest.mark.asyncio
async def test_real_tcp_registration_requests_and_disconnect(
    monkeypatch,
    caplog,
    manager,
):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(registry.settings, "SECRET_KEY", TEST_KEY)
    token = jwt.encode(
        {
            "type": "plugin_dev",
            "project_id": "owned",
            "user_id": "staff",
        },
        TEST_KEY,
        algorithm="HS256",
    )
    finished = asyncio.Event()

    async def accept(reader, writer):
        try:
            await socket_server._handle_client(reader, writer)
        finally:
            finished.set()

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    reader, writer = await asyncio.open_connection(
        "127.0.0.1",
        server.sockets[0].getsockname()[1],
    )
    try:
        writer.write(
            frame(
                {
                    "id": 1,
                    "method": "register",
                    "params": {
                        "id": "tcp-350",
                        "name": PRIVATE,
                        "version": "1",
                        "capabilities": [],
                        "dev_token": token,
                    },
                }
            )
        )
        await writer.drain()
        registration = await asyncio.wait_for(
            socket_server._recv_message(reader),
            2,
        )
        assert registration["result"]["plugin_id"] == "tcp-350"
        plugin = manager.get_plugin("tcp-350", project_id="owned")
        assert plugin and plugin.is_dev_mode
        for failed in [True, False]:
            pending = asyncio.create_task(
                manager.send_request(
                    "tcp-350",
                    "tool/execute",
                    {"value": PRIVATE},
                    project_id="owned",
                    timeout=2,
                )
            )
            try:
                request = await asyncio.wait_for(
                    socket_server._recv_message(reader),
                    2,
                )
                assert request["params"] == {"value": PRIVATE}
                reply = {"id": request["id"]}
                reply["error" if failed else "result"] = (
                    {"message": PRIVATE} if failed else {"text": "ok"}
                )
                writer.write(frame(reply))
                await writer.drain()
                assert await asyncio.wait_for(pending, 2) == (
                    None if failed else {"text": "ok"}
                )
                assert not plugin._pending_requests
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    finally:
        writer.close()
        await writer.wait_closed()
        server.close()
        await server.wait_closed()
        await asyncio.wait_for(finished.wait(), 2)
    assert not manager.plugins
    assert_private(caplog.records)
