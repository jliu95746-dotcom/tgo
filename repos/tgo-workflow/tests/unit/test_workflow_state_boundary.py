"""Approval reads and audit transport preserve boundary contracts."""

from copy import deepcopy
from unittest.mock import AsyncMock, call
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from app.models.approval import WorkflowApprovalRequest
from app.models.execution import WorkflowExecution
from app.services.approval_state import approval_customer_session
from app.services.event_source_audit import (
    ADVANCE,
    CURSOR_SECONDS,
    RELEASE,
    RedisAuditProgress,
)
from app.services.execution_snapshot import definition_digest


@pytest.mark.parametrize(
    "data, available",
    [
        (None, False),
        ([], False),
        ("synthetic-private-state-marker", False),
        (7, False),
        ({"after_approval_action": []}, False),
        ({"after_approval_action": "create_aftersales_review"}, True),
    ],
)
@pytest.mark.parametrize(
    "request_status, run_status",
    [("pending", "waiting_approval"), ("approved", "pending_resume")],
)
def test_approval_session_requires_frozen_action_object(
    data, available, request_status, run_status
):
    session = str(uuid4())
    definition = {
        "nodes": [{"id": "approval", "type": "approval", "data": data}]
    }
    original = deepcopy(definition)
    run = WorkflowExecution(
        id="run", project_id="project", workflow_id="workflow",
        status=run_status, execution_mode="production",
        active_approval_id="approval", employee_agent_id="employee",
        business_request_id="request", visitor_session_id=session,
        definition_snapshot=definition,
        definition_digest=definition_digest(definition),
    )
    request = WorkflowApprovalRequest(
        id="approval", execution_id="run", project_id="project",
        node_id="approval", status=request_status,
    )
    assert approval_customer_session(run, request) == (
        session if available else None
    )
    assert run.definition_snapshot == original
    assert run.status == run_status and request.status == request_status


@pytest.mark.asyncio
async def test_progress_uses_same_eval_wire_arguments(monkeypatch):
    # No connection is opened: capture the actual async client's command call.
    broker = Redis(host="127.0.0.1", port=1)
    command = AsyncMock(side_effect=[1, 0, 1])
    monkeypatch.setattr(broker, "execute_command", command)
    progress = RedisAuditProgress(broker, "synthetic-audit")
    try:
        assert await progress.advance("owner", "cursor")
        assert not await progress.advance("old-owner", "cursor")
        await progress.release("owner")
        assert command.await_args_list == [
            call(
                "EVAL", ADVANCE, 2, "synthetic-audit:lease",
                "synthetic-audit:cursor", "owner", "cursor",
                str(CURSOR_SECONDS),
            ),
            call(
                "EVAL", ADVANCE, 2, "synthetic-audit:lease",
                "synthetic-audit:cursor", "old-owner", "cursor",
                str(CURSOR_SECONDS),
            ),
            call("EVAL", RELEASE, 1, "synthetic-audit:lease", "owner"),
        ]
    finally:
        await broker.aclose()
