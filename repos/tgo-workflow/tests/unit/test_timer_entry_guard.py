"""Knowing a timer workflow ID cannot replace a registered plan grant."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from fastapi import HTTPException
import pytest
from app.api import executions
from app.schemas.execution import WorkflowExecuteRequest


@pytest.mark.asyncio
async def test_direct_production_timer_cannot_bypass_its_registered_plan(
    monkeypatch,
):
    graph = {
        "nodes": [{"type": "timer", "id": "clock", "data": {}}],
        "edges": [],
    }
    monkeypatch.setattr(
        executions.WorkflowService,
        "get_by_id",
        AsyncMock(
            return_value=SimpleNamespace(
                status="active", definition=graph, version=1
            )
        ),
    )
    monkeypatch.setattr(
        executions,
        "production_definition",
        AsyncMock(
            return_value=SimpleNamespace(
                definition=graph, version=1, release_id="fixed"
            )
        ),
    )
    create = AsyncMock()
    monkeypatch.setattr(executions, "_create_execution_record", create)
    with pytest.raises(HTTPException) as denied:
        await executions.execute_workflow(
            "owned",
            WorkflowExecuteRequest(execution_mode="production"),
            "project",
            AsyncMock(),
        )
    assert denied.value.status_code == 403
    create.assert_not_awaited()
