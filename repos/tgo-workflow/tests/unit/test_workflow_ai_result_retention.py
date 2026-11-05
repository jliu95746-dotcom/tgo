"""The bounded reaper survives errors, hides payloads and stops at shutdown."""
import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.services import workflow_ai_result_retention as retention


@pytest.mark.asyncio
async def test_reaper_hides_private_errors_and_propagates_cancellation(
    monkeypatch, caplog
):
    @asynccontextmanager
    async def session():
        yield object()

    monkeypatch.setattr(retention, "AsyncSessionLocal", session)
    purge = AsyncMock(side_effect=ValueError("private-cache-body-do-not-log"))
    monkeypatch.setattr(retention, "purge", purge)
    sleep = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(retention.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await retention.reap_forever()
    purge.assert_awaited_once()
    sleep.assert_awaited_once_with(60)
    assert "ValueError" in caplog.text
    assert "private-cache-body-do-not-log" not in caplog.text


@pytest.mark.asyncio
async def test_application_shutdown_cancels_reaper_and_closes_http(
    monkeypatch,
):
    from app import main

    started, stopped = asyncio.Event(), asyncio.Event()

    async def reaper():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(main, "reap_forever", reaper)
    close = AsyncMock()
    monkeypatch.setattr(main.HttpClient, "close_client", close)
    async with main.lifespan(main.app):
        await asyncio.wait_for(started.wait(), 2)
    assert stopped.is_set()
    close.assert_awaited_once()
