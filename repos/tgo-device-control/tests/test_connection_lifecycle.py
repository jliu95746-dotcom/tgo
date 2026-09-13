"""Regression tests for per-connection ownership and shutdown."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from app.services.tcp_connection_manager import TcpConnectionManager
from app.config import settings


@pytest_asyncio.fixture
async def manager(monkeypatch):
    monkeypatch.setattr(TcpConnectionManager, "_instance", None)
    instance = TcpConnectionManager()
    yield instance
    await instance.shutdown()


async def connect(manager, device_id="device-one"):
    writer = MagicMock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    return await manager.register_connection(
        device_id, "Test device", "1", [], asyncio.StreamReader(), writer
    )


@pytest.mark.asyncio
async def test_stale_cleanup_cannot_close_replacement(manager):
    old = await connect(manager)
    new = await connect(manager)
    assert not await manager.unregister_connection(
        old.agent_id, expected_connection=old
    )
    assert manager.get_connection(new.agent_id) is new
    new.writer.close.assert_not_called()
    old.writer.close.assert_called_once()


@pytest.mark.asyncio
async def test_stale_heartbeat_cannot_refresh_replacement(manager):
    old = await connect(manager)
    new = await connect(manager)
    last_seen = datetime(2020, 1, 1)
    new.last_seen = last_seen
    manager.update_heartbeat(old.agent_id, expected_connection=old)
    assert new.last_seen == last_seen
    manager.update_heartbeat(new.agent_id, expected_connection=new)
    assert new.last_seen > last_seen


@pytest.mark.asyncio
@pytest.mark.parametrize("replace", [False, True])
async def test_disconnect_finishes_pending_without_cancelling_caller(manager, replace):
    old = await connect(manager)
    request = asyncio.create_task(old.send_request("tools/call", timeout=30))
    await asyncio.sleep(0)
    assert old._pending_requests
    if replace:
        await connect(manager)
    else:
        await manager.unregister_connection(old.agent_id)
    done, _ = await asyncio.wait({request}, timeout=0.1)
    try:
        assert request in done, "pending tool call survived connection close"
        assert not request.cancelled(), "disconnect cancelled the HTTP caller"
        assert request.result() is None
        assert not old._pending_requests
    finally:
        request.cancel()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.asyncio
async def test_initialize_is_idempotent(manager):
    await manager.initialize()
    first = manager._heartbeat_task
    try:
        await manager.initialize()
        assert manager._heartbeat_task is first
    finally:
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.asyncio
async def test_lifecycle_lock_serializes_same_device_only(manager):
    entered = asyncio.Event()

    async def wait_for_same_device():
        async with manager.lifecycle_lock("one"):
            entered.set()

    async with manager.lifecycle_lock("one"):
        task = asyncio.create_task(wait_for_same_device())
        await asyncio.sleep(0)
        assert not entered.is_set()
        async with manager.lifecycle_lock("two"):
            assert not entered.is_set()
    await asyncio.wait_for(task, 1)
    assert entered.is_set()


@pytest.mark.asyncio
async def test_heartbeat_stale_snapshot_cannot_remove_replacement(manager, monkeypatch):
    old = await connect(manager)
    replacements = []
    checked = asyncio.Event()

    async def replace_while_ping_pending(message):
        replacements.append(await connect(manager))
        checked.set()
        raise ConnectionError("old transport closed")

    old.send_message = replace_while_ping_pending
    monkeypatch.setattr(settings, "HEARTBEAT_INTERVAL", 0.01)
    await manager.initialize()
    await asyncio.wait_for(checked.wait(), 1)
    await asyncio.sleep(0.03)
    assert manager.get_connection(old.agent_id) is replacements[0]
