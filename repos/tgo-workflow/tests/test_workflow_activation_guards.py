from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from fastapi import HTTPException
from app.api.workflows import update_workflow
from app.api.executions import execute_workflow
from app.schemas.workflow import WorkflowUpdate
from app.schemas.execution import WorkflowExecuteRequest


def unsupported_workflow():
    return SimpleNamespace(status="draft", version=1, published_version=None, definition={"nodes": [
        {"id": "input", "type": "input", "data": {}},
        {"id": "parallel", "type": "parallel", "data": {}},
        {"id": "answer", "type": "answer", "data": {"output_type": "template", "output_template": "test"}},
    ], "edges": [{"source": "input", "target": "parallel"}, {"source": "parallel", "target": "answer"}]})


@pytest.mark.asyncio
async def test_generic_update_cannot_bypass_publish_validation():
    mutation = AsyncMock()
    with patch("app.api.workflows.WorkflowService.get_by_id", AsyncMock(return_value=unsupported_workflow())), patch("app.api.workflows.WorkflowService.update", mutation):
        with pytest.raises(HTTPException) as caught:
            await update_workflow("workflow", WorkflowUpdate(status="active"), "project", AsyncMock())
    assert caught.value.status_code == 409
    mutation.assert_not_awaited()


@pytest.mark.asyncio
async def test_incomplete_draft_can_be_saved_without_being_enabled():
    mutation = AsyncMock(return_value=unsupported_workflow())
    with patch("app.api.workflows.WorkflowService.get_by_id", AsyncMock(return_value=unsupported_workflow())), patch("app.api.workflows.WorkflowService.update", mutation):
        result = await update_workflow("workflow", WorkflowUpdate(status="draft"), "project", AsyncMock())
    assert result.status == "draft"
    mutation.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_workflow_is_rejected_before_any_execution_record_or_dispatch():
    create = AsyncMock()
    with patch("app.api.executions.WorkflowService.get_by_id", AsyncMock(return_value=unsupported_workflow())), patch("app.api.executions._create_execution_record", create):
        with pytest.raises(HTTPException) as caught:
            await execute_workflow("workflow", WorkflowExecuteRequest(inputs={}), "project", AsyncMock())
    assert caught.value.status_code == 400
    create.assert_not_awaited()
