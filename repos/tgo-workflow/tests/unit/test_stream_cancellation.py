"""HTTP execution must not revive cancelled runs or leak executor tasks."""

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.api import executions


class StateDatabase:
    def __init__(self, status):
        self.status = status
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.close = AsyncMock()
        self.add = Mock()

    async def execute(self, statement):
        values = statement.compile().params
        if statement.is_select:
            value = self.status
        else:
            condition = values.get("status_1")
            allowed = condition is None or (
                self.status in condition if isinstance(condition, list)
                else self.status == condition
            )
            if allowed:
                self.status = values["status"]
            value = "execution-owned" if allowed else None
        return Mock(scalar_one_or_none=Mock(return_value=value))


def arguments(db):
    return ("workflow-owned", "project-owned", "execution-owned", {}, {},
            datetime.utcnow(), db)


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_cancelled_execution_never_turns_into_success(
    monkeypatch, stream,
):
    db = StateDatabase("cancelled")
    touched = []

    async def run(inputs, on_node_start=None, on_node_complete=None):
        if on_node_start:
            await on_node_start("node", "api", {"label": "节点"}, 1)
        touched.append("remote-call")
        return "late-output"

    monkeypatch.setattr(
        executions, "WorkflowExecutor",
        Mock(return_value=SimpleNamespace(run=run)),
    )
    if stream:
        events = [json.loads(frame[6:]) async for frame in
                  executions._run_stream_execution(*arguments(db))]
        assert events[-1]["data"]["status"] == "cancelled"
    else:
        result = await executions._run_sync_execution(*arguments(db))
        assert not result["success"]
    assert db.status == "cancelled"
    assert not touched


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect", ["close", "cancel"])
async def test_stream_disconnect_stops_executor_and_records_cancellation(
    monkeypatch, disconnect,
):
    db = StateDatabase("running")
    stopped = asyncio.Event()
    tasks = []

    async def run(inputs, on_node_start=None, on_node_complete=None):
        tasks.append(asyncio.current_task())
        try:
            await on_node_start("node", "api", {"label": "节点"}, 1)
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(
        executions, "WorkflowExecutor",
        Mock(return_value=SimpleNamespace(run=run)),
    )
    stream = executions._run_stream_execution(*arguments(db))
    try:
        await stream.__anext__()  # workflow_started
        await stream.__anext__()  # node_started
        if disconnect == "close":
            await stream.aclose()
        else:
            waiting = asyncio.create_task(stream.__anext__())
            await asyncio.sleep(0)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
        assert stopped.is_set()
        assert db.status == "cancelled"
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await stream.aclose()


@pytest.mark.asyncio
async def test_disconnect_after_initial_event_closes_owned_session(
    monkeypatch,
):
    db = StateDatabase("running")
    monkeypatch.setattr(executions, "AsyncSessionLocal", Mock(return_value=db))
    stream = executions._stream_with_owned_session(*arguments(db)[:-1])
    await stream.__anext__()
    await stream.aclose()
    assert db.status == "cancelled"
    db.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_execution_timestamps_are_timezone_aware():
    db = StateDatabase("running")
    row = await executions._create_execution_record(
        db, "workflow-owned", "project-owned", {},
    )
    assert row.started_at.tzinfo is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_late_result_cannot_overwrite_a_concurrent_cancel(
    monkeypatch, stream,
):
    db = StateDatabase("running")

    async def run(*_, **__):
        db.status = "cancelled"
        return "late result"

    monkeypatch.setattr(executions, "WorkflowExecutor", Mock(
        return_value=SimpleNamespace(run=run),
    ))
    if stream:
        events = [json.loads(frame[6:]) async for frame in
                  executions._run_stream_execution(*arguments(db))]
        assert events[-1]["data"]["status"] == "cancelled"
        assert events[-1]["data"]["outputs"] != "late result"
    else:
        result = await executions._run_sync_execution(*arguments(db))
        assert not result["success"] and result["output"] != "late result"
    assert db.status == "cancelled"
