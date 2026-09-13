"""Queued runs cannot outlive failed dispatch, cancellation, or completion."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.api import executions
from app.schemas.execution import WorkflowExecuteRequest
from celery_app import tasks
from app.services import execution_lifecycle, workflow_dispatch
from app.integrations.http_client import HttpClient


def setup_dispatch(monkeypatch, failure=None):
    row = SimpleNamespace(
        id="execution-owned", workflow_id="workflow-owned",
        project_id="project-owned", status="pending",
    )
    monkeypatch.setattr(
        executions.WorkflowService, "get_by_id",
        AsyncMock(return_value=SimpleNamespace(definition={})),
    )
    monkeypatch.setattr(
        executions, "_create_execution_record", AsyncMock(return_value=row),
    )
    publisher = Mock(side_effect=failure)
    monkeypatch.setattr(tasks.execute_workflow_task, "delay", publisher)
    monkeypatch.setattr(tasks.execute_workflow_task, "apply_async", publisher)
    db = AsyncMock()
    db.execute.return_value = Mock(
        scalar_one=Mock(return_value=row),
        scalar_one_or_none=Mock(return_value="execution-owned"),
    )
    return db, publisher


@pytest.mark.asyncio
async def test_dispatch_failure_is_reported_and_recorded(monkeypatch):
    db, _ = setup_dispatch(monkeypatch, OSError("private-broker-uri"))
    with pytest.raises(HTTPException) as error:
        await executions.execute_workflow(
            "workflow-owned", WorkflowExecuteRequest(**{"async": True}),
            "project-owned", db,
        )
    assert error.value.status_code == 503
    assert "private-broker-uri" not in str(error.value.detail)
    failed = db.execute.await_args_list[0].args[0].compile().params
    assert "failed" in failed.values()
    assert "pending" in failed.values()
    assert "project-owned" in failed.values()
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_published_task_uses_execution_id_and_no_hidden_retries(
    monkeypatch,
):
    db, publisher = setup_dispatch(monkeypatch)
    await executions.execute_workflow(
        "workflow-owned", WorkflowExecuteRequest(**{"async": True}),
        "project-owned", db,
    )
    assert publisher.call_args.kwargs["task_id"] == "execution-owned"
    assert publisher.call_args.kwargs["retry"] is False


@pytest.mark.asyncio
async def test_lost_ack_does_not_overwrite_a_claimed_run(monkeypatch):
    db, _ = setup_dispatch(monkeypatch, OSError("lost acknowledgement"))
    db.execute.return_value.scalar_one_or_none.return_value = None
    await executions.execute_workflow(
        "workflow-owned", WorkflowExecuteRequest(**{"async": True}),
        "project-owned", db,
    )


@pytest.mark.asyncio
async def test_slow_broker_call_has_a_bounded_wait(monkeypatch):
    db, _ = setup_dispatch(monkeypatch)
    row = SimpleNamespace(
        id="execution-owned", workflow_id="workflow-owned",
        project_id="project-owned",
    )

    async def timeout(awaitable, timeout):
        awaitable.close()
        assert timeout == workflow_dispatch.BROKER_CALL_TIMEOUT
        raise TimeoutError("owned slow broker")

    monkeypatch.setattr(workflow_dispatch.asyncio, "wait_for", timeout)
    with pytest.raises(workflow_dispatch.WorkflowDispatchError):
        await workflow_dispatch.enqueue_workflow(db, row, {})


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["cancelled", "failed", "completed", None])
async def test_no_more_nodes_after_execution_stops(status):
    db = AsyncMock()
    db.execute.return_value = Mock(
        scalar_one_or_none=Mock(return_value=status),
    )
    with pytest.raises(execution_lifecycle.ExecutionStopped):
        await execution_lifecycle.require_running(
            db, "execution-owned", "project-owned",
        )


@pytest.mark.asyncio
async def test_late_completion_only_updates_a_running_owned_record():
    db = AsyncMock()
    db.execute.return_value = Mock(
        scalar_one_or_none=Mock(return_value=None),
    )
    assert not await execution_lifecycle.finish_running_execution(
        db, "execution-owned", "project-owned", "completed", "answer", None, 1,
    )
    values = db.execute.await_args.args[0].compile().params
    assert "running" in values.values()
    assert "project-owned" in values.values()


@pytest.mark.asyncio
async def test_worker_does_not_run_an_unclaimable_execution(monkeypatch):
    db = AsyncMock()
    db.execute.return_value = Mock(
        scalar_one_or_none=Mock(return_value=None),
    )
    session = Mock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=db),
    ))
    monkeypatch.setattr(tasks, "workflow_task_session", session)
    monkeypatch.setattr(
        tasks.WorkflowService, "get_by_id",
        AsyncMock(return_value=SimpleNamespace(definition={})),
    )
    executor = Mock(return_value=SimpleNamespace(run=AsyncMock()))
    monkeypatch.setattr(tasks, "WorkflowExecutor", executor)
    await tasks.async_execute_workflow(
        "execution-owned", "workflow-owned", {}, "project-owned",
    )
    executor.assert_not_called()
    claim = db.execute.await_args_list[0].args[0].compile().params
    assert "pending" in claim.values()
    assert "project-owned" in claim.values()
    assert "workflow-owned" in claim.values()


@pytest.mark.asyncio
@pytest.mark.parametrize("close_error", [None, RuntimeError("owned cleanup")])
async def test_task_closes_http_connections_before_its_event_loop_ends(
    monkeypatch, close_error,
):
    db = AsyncMock()
    session = Mock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=db),
    ))
    monkeypatch.setattr(tasks, "workflow_task_session", session)
    monkeypatch.setattr(
        tasks.WorkflowService, "get_by_id", AsyncMock(return_value=None),
    )
    client = SimpleNamespace(
        is_closed=False, aclose=AsyncMock(side_effect=close_error),
    )
    monkeypatch.setattr(HttpClient, "_client", client)
    await tasks.async_execute_workflow(
        "execution-owned", "workflow-owned", {}, "project-owned",
    )
    client.aclose.assert_awaited_once()
    assert HttpClient._client is None


@pytest.mark.asyncio
@pytest.mark.parametrize("already_cancelled", [False, True])
async def test_cancel_is_durable_even_if_broker_is_unavailable(
    monkeypatch, already_cancelled,
):
    row = SimpleNamespace(
        id="execution-owned",
        status="cancelled" if already_cancelled else "pending",
        completed_at=datetime.now(timezone.utc),
    )
    db = AsyncMock()
    db.execute.return_value = Mock(
        scalar_one_or_none=Mock(return_value=row),
    )
    revoke = Mock(side_effect=OSError("private-broker-uri"))
    monkeypatch.setattr(workflow_dispatch.celery_app.control, "revoke", revoke)
    result = await executions.cancel_execution(
        "execution-owned", "project-owned", db,
    )
    assert result.status == "cancelled"
    assert row.status == "cancelled"
    if not already_cancelled:
        assert revoke.call_args.kwargs.get("terminate") is False
        db.commit.assert_awaited()
