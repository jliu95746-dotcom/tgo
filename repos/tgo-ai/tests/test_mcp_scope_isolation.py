"""Request cleanup is isolated, LIFO, and does not leak connection errors."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from app.runtime.core.exceptions import MCPToolError
from app.runtime.tools.mcp_lifecycle import mcp_request_scope, own_mcp_connection


@pytest.mark.asyncio
async def test_nested_scopes_restore_parent_and_close_in_reverse_order():
    closed = []

    def connection(name):
        return Mock(close=AsyncMock(side_effect=lambda: closed.append(name)))

    async with mcp_request_scope():
        own_mcp_connection(connection("outer-first"))
        async with mcp_request_scope():
            own_mcp_connection(connection("inner"))
        assert closed == ["inner"]
        own_mcp_connection(connection("outer-second"))
    assert closed == ["inner", "outer-second", "outer-first"]
    # No request scope: low-level callers must close their own connections.
    manual = connection("manual")
    own_mcp_connection(manual)
    manual.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_parallel_requests_do_not_close_each_others_connections():
    first_ready, second_ready, release_second = asyncio.Event(), asyncio.Event(), asyncio.Event()
    first, second = Mock(close=AsyncMock()), Mock(close=AsyncMock())

    async def run_first():
        async with mcp_request_scope():
            own_mcp_connection(first)
            first_ready.set()
            await second_ready.wait()

    async def run_second():
        await first_ready.wait()
        async with mcp_request_scope():
            own_mcp_connection(second)
            second_ready.set()
            await release_second.wait()

    first_task, second_task = asyncio.create_task(run_first()), asyncio.create_task(run_second())
    try:
        await asyncio.wait_for(first_task, 2)
        first.close.assert_awaited_once()
        second.close.assert_not_awaited()
    finally:
        release_second.set()
        await asyncio.gather(first_task, second_task)
    second.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_failure_still_attempts_earlier_connections_without_secret(monkeypatch):
    logger = Mock()
    monkeypatch.setattr("app.runtime.tools.mcp_lifecycle._logger", logger)
    first = Mock(close=AsyncMock())
    second = Mock(close=AsyncMock(side_effect=OSError("Authorization: fixture-secret")))
    with pytest.raises(MCPToolError, match="工具连接清理失败") as raised:
        async with mcp_request_scope():
            own_mcp_connection(first)
            own_mcp_connection(second)
    first.close.assert_awaited_once()
    second.close.assert_awaited_once()
    assert "fixture-secret" not in str(raised.value)
    assert "fixture-secret" not in str(logger.mock_calls)
