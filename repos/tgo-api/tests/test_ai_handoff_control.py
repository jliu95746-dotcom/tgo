"""A deliberate handoff completes reply control without emitting a false failure."""

from unittest.mock import AsyncMock
import pytest
from app.schemas.ai_runs import ReplyRun
from app.services import ai_reply_control as control


@pytest.mark.asyncio
async def test_handoff_is_a_completed_nonbillable_terminal(monkeypatch):
    identity = ReplyRun(project_id="synthetic", channel_id="synthetic-vtr", channel_type=251, client_msg_no="synthetic-handoff")
    registry = AsyncMock()
    registry.finish.return_value = None
    registry.heartbeat.return_value = identity
    monkeypatch.setattr(control, "run_registry", registry)
    publish = AsyncMock()
    async def source():
        yield {"event_type": "human_handoff", "data": {"message": "转人工"}}
    events = [event async for event in control.controlled_reply(identity, source, publish, AsyncMock())]
    assert [event["event_type"] for event in events] == ["human_handoff"]
    publish.assert_not_awaited()
    assert registry.finish.call_args.args[1] == "completed"
