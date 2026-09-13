"""Real local TCP transport with isolated mocked persistence, no device commands."""

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from app.services import tcp_rpc_server as server_module
from app.services.tcp_connection_manager import TcpConnectionManager
from app.services.tcp_rpc_server import TcpRpcServer


@pytest_asyncio.fixture
async def server(monkeypatch):
    monkeypatch.setattr(TcpConnectionManager, "_instance", None)
    manager = TcpConnectionManager()
    monkeypatch.setattr(server_module, "tcp_connection_manager", manager)
    device_id, project_id = uuid.uuid4(), uuid.uuid4()
    device = SimpleNamespace(
        id=device_id, project_id=project_id, device_token="test-token"
    )
    monkeypatch.setattr(
        server_module.DeviceService,
        "get_device_by_token",
        AsyncMock(return_value=device),
    )
    monkeypatch.setattr(
        server_module.DeviceService, "update_device_status", AsyncMock()
    )
    session = MagicMock()
    session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
    session.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(server_module, "AsyncSessionLocal", session)
    instance = TcpRpcServer("127.0.0.1", 0)
    instance._authenticate = AsyncMock(
        return_value=(
            str(device_id),
            "test-token",
            str(project_id),
            "Fixture",
            "1",
            False,
        )
    )
    instance._update_device_offline = AsyncMock()
    await instance.start()
    clients = []
    try:
        yield instance, manager, str(device_id), clients
    finally:
        for writer, pump in clients:
            writer.close()
            await writer.wait_closed()
            await asyncio.wait_for(pump, 2)
        await instance.stop()
        await manager.shutdown()
        await asyncio.sleep(0.05)


async def client(server):
    instance, _, _, clients = server
    port = instance.server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(
        json.dumps({"jsonrpc": "2.0", "id": 0, "method": "auth", "params": {}}).encode()
        + b"\n"
    )
    await writer.drain()
    reply = json.loads(await asyncio.wait_for(reader.readline(), 2))

    async def pump():
        while line := await reader.readline():
            message = json.loads(line)
            if "id" in message:
                writer.write(
                    json.dumps({"id": message["id"], "result": {"tools": []}}).encode()
                    + b"\n"
                )
                await writer.drain()

    task = asyncio.create_task(pump())
    clients.append((writer, task))
    return reply


@pytest.mark.asyncio
async def test_old_reader_cleanup_keeps_new_connection_online(server):
    instance, manager, device_id, _ = server
    assert (await client(server))["result"]["status"] == "ok"
    old = manager.get_connection(device_id)
    assert (await client(server))["result"]["status"] == "ok"
    new = manager.get_connection(device_id)
    assert new is not old
    await asyncio.sleep(0.1)
    assert manager.get_connection(device_id) is new
    instance._update_device_offline.assert_not_awaited()
    assert await new.list_tools(timeout=1) == []


@pytest.mark.asyncio
async def test_deleted_token_cannot_install_inflight_authenticated_connection(
    server, monkeypatch
):
    instance, manager, device_id, _ = server
    monkeypatch.setattr(
        server_module.DeviceService, "get_device_by_token", AsyncMock(return_value=None)
    )
    reply = await client(server)
    assert "error" in reply
    assert manager.get_connection(device_id) is None


@pytest.mark.asyncio
async def test_tcp_bind_failure_is_not_reported_as_started():
    occupied = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = occupied.sockets[0].getsockname()[1]
    instance = TcpRpcServer("127.0.0.1", port)
    try:
        with pytest.raises(OSError):
            await instance.start()
    finally:
        await instance.stop()
        occupied.close()
        await occupied.wait_closed()


@pytest.mark.asyncio
async def test_disconnect_during_tool_discovery_is_cleaned_up_promptly(server):
    instance, manager, device_id, _ = server
    port = instance.server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b'{"id":0,"method":"auth","params":{}}\n')
    await writer.drain()
    assert "result" in json.loads(await reader.readline())
    assert json.loads(await reader.readline())["method"] == "tools/list"
    writer.close()
    await writer.wait_closed()
    for _ in range(50):
        if (manager.get_connection(device_id) is None
                and instance._update_device_offline.await_count):
            break
        await asyncio.sleep(0.01)
    assert manager.get_connection(device_id) is None
    instance._update_device_offline.assert_awaited_once_with(device_id)


@pytest.mark.asyncio
async def test_cancelled_reconnect_does_not_leave_unowned_connection(server):
    instance, manager, device_id, _ = server
    closing = asyncio.Event()

    async def wait_for_old_close():
        closing.set()
        await asyncio.Event().wait()

    old_writer = MagicMock()
    old_writer.wait_closed = wait_for_old_close
    await manager.register_connection(
        device_id, "old", "1", [], asyncio.StreamReader(), old_writer
    )
    new_writer = MagicMock()
    new_writer.wait_closed = AsyncMock()
    project_id = instance._authenticate.return_value[2]
    task = asyncio.create_task(instance._activate_connection(
        device_id, "test-token", project_id, "new", "1",
        asyncio.StreamReader(), new_writer,
    ))
    await asyncio.wait_for(closing.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert manager.get_connection(device_id) is None
    new_writer.close.assert_called_once()
