"""Nested workflow calls must stop before entering a cycle or excessive depth."""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.workflow_lineage import extend_workflow_lineage, require_parent_execution
from app.schemas.execution import WorkflowExecutionOrigin
from uuid import uuid4


def test_nested_workflow_lineage_rejects_direct_and_indirect_cycles():
    assert extend_workflow_lineage((), "workflow-a") == ("workflow-a",)
    assert extend_workflow_lineage(("workflow-a",), "workflow-b") == (
        "workflow-a", "workflow-b",
    )
    with pytest.raises(ValueError, match="循环"):
        extend_workflow_lineage(("workflow-a",), "workflow-a")
    with pytest.raises(ValueError, match="循环"):
        extend_workflow_lineage(("workflow-a", "workflow-b"), "workflow-a")


def test_nested_workflow_lineage_rejects_excessive_depth():
    with pytest.raises(ValueError, match="层级"):
        extend_workflow_lineage(("workflow-a", "workflow-b", "workflow-c", "workflow-d"), "workflow-e")


def test_parent_origin_requires_complete_coordinates():
    with pytest.raises(ValueError, match="同时提供"):
        WorkflowExecutionOrigin(ai_request_id="run-a", parent_execution_id=uuid4())
    with pytest.raises(ValueError, match="同时提供"):
        WorkflowExecutionOrigin(ai_request_id="run-a", workflow_lineage=("workflow-a",))


@pytest.mark.asyncio
async def test_parent_run_must_be_current_and_from_same_project():
    parent_id = uuid4()
    origin = WorkflowExecutionOrigin(
        ai_request_id="run-a", parent_execution_id=parent_id,
        workflow_lineage=("workflow-a",),
    )
    db = AsyncMock()
    db.get.return_value = SimpleNamespace(
        project_id="owned-project", status="running",
        workflow_id="workflow-a", workflow_lineage=["workflow-a"],
        business_request_id=None, visitor_session_id=None,
        source_message_id=None, source_channel=None,
    )
    await require_parent_execution(db, origin, "owned-project")
    db.get.assert_awaited_once()
    db.get.return_value.status = "completed"
    with pytest.raises(PermissionError):
        await require_parent_execution(db, origin, "owned-project")
    db.get.return_value.status = "running"
    with pytest.raises(PermissionError):
        await require_parent_execution(db, origin, "other-project")
    db.get.return_value.workflow_lineage = ["workflow-b"]
    with pytest.raises(PermissionError):
        await require_parent_execution(db, origin, "owned-project")


@pytest.mark.asyncio
async def test_nested_run_inherits_verified_customer_coordinates():
    project_id, request_id, session_id = uuid4(), uuid4(), uuid4()
    origin = WorkflowExecutionOrigin(
        ai_request_id="nested-ai", parent_execution_id=uuid4(),
        workflow_lineage=("workflow-a",),
    )
    db = AsyncMock()
    db.get.return_value = SimpleNamespace(
        project_id=str(project_id), status="running",
        workflow_id="workflow-a", workflow_lineage=["workflow-a"],
        business_request_id=str(request_id), visitor_session_id=str(session_id),
        source_message_id=None, source_channel=None,
    )
    resolved = await require_parent_execution(db, origin, str(project_id))
    assert resolved.business_project_id == project_id
    assert resolved.business_request_id == request_id
    assert resolved.visitor_session_id == session_id
    assert origin.business_request_id is None
    mismatched = origin.model_copy(update={
        "business_project_id": project_id,
        "business_request_id": uuid4(),
        "visitor_session_id": session_id,
    })
    with pytest.raises(PermissionError):
        await require_parent_execution(db, mismatched, str(project_id))
