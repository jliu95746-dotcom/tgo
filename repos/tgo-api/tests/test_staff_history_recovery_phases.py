"""One failed recovery phase must not suppress later committed effects."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.tasks import recover_staff_delivery_history as recovery


@pytest.mark.asyncio
async def test_notice_recovery_failure_does_not_skip_channel_effects(monkeypatch):
    connection = MagicMock()
    monkeypatch.setattr(recovery, "SessionLocal", lambda: connection)
    monkeypatch.setattr(recovery, "recover_pending_history", AsyncMock())
    monkeypatch.setattr(recovery, "recover_pending_training", AsyncMock())
    monkeypatch.setattr(
        recovery, "recover_session_commands", AsyncMock(side_effect=RuntimeError())
    )
    effects = AsyncMock()
    monkeypatch.setattr(recovery, "recover_channel_effects", effects)
    monkeypatch.setattr(
        recovery.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError())
    )

    with pytest.raises(asyncio.CancelledError):
        await recovery._recover_loop()

    effects.assert_awaited_once()
