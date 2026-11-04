"""A workflow approval node must pause before downstream work can run."""

from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api import executions
from app.engine.checkpoint import WorkflowPaused
from app.engine.executor import WorkflowExecutor
from app.schemas.execution import WorkflowExecuteRequest
from app.services.validation_service import ValidationService


def graph(approver_id: str | None = None):
    return {
        "nodes": [
            {"id": "start", "type": "input", "data": {
                "type": "input", "label": "输入", "reference_key": "input",
                "input_variables": [],
            }},
            {"id": "approval", "type": "approval", "data": {
                "type": "approval", "label": "人工确认", "reference_key": "approval",
                "approver_staff_id": approver_id or str(uuid4()),
                "summary": "核对后继续办理", "expires_in_minutes": 60,
            }},
            {"id": "finish", "type": "answer", "data": {
                "type": "answer", "label": "返回结果", "reference_key": "finish",
                "output_type": "template", "output_template": "确认后执行完成",
            }},
        ],
        "edges": [
            {"source": "start", "target": "approval"},
            {"source": "approval", "target": "finish"},
        ],
    }


def test_approval_configuration_requires_a_real_assignee_and_bounded_deadline():
    assert ValidationService.validate_workflow(graph()) == []
    invalid = graph()
    invalid["nodes"][1]["data"]["approver_staff_id"] = "not-a-uuid"
    assert ValidationService.validate_workflow(invalid)
    invalid = graph()
    invalid["nodes"][1]["data"]["expires_in_minutes"] = 10081
    assert ValidationService.validate_workflow(invalid)
    invalid = graph()
    invalid["nodes"][1]["data"]["summary"] = "   "
    assert ValidationService.validate_workflow(invalid)


def test_approval_cannot_claim_to_authorize_unpreviewed_business_actions():
    definition = graph()
    definition["nodes"].insert(2, {
        "id": "action", "type": "tool", "data": {
            "type": "tool", "label": "业务动作", "reference_key": "action",
            "tool_id": "tool-1",
        },
    })
    definition["edges"] = [
        {"source": "start", "target": "approval"},
        {"source": "approval", "target": "action"},
        {"source": "action", "target": "finish"},
    ]
    assert any("审批" in error for error in ValidationService.validate_workflow(definition))
    definition = graph()
    definition["nodes"].insert(2, {
        "id": "model", "type": "llm", "data": {
            "type": "llm", "label": "带工具模型", "reference_key": "model",
            "provider_id": "model-provider", "model_id": "model-a",
            "user_prompt": "确认后继续", "tool_ids": ["write-tool"],
        },
    })
    assert any("审批" in error for error in ValidationService.validate_workflow(definition))


def test_aftersales_review_requires_bounded_category():
    definition = graph()
    definition["nodes"][1]["data"]["after_approval_action"] = "create_aftersales_review"
    assert any("售后核对类别" in error for error in ValidationService.validate_workflow(definition))
    definition["nodes"][1]["data"]["case_category"] = "return_request"
    assert ValidationService.validate_workflow(definition) == []
    definition["nodes"][1]["data"]["case_category"] = "refund_payment"
    assert any("售后核对类别" in error for error in ValidationService.validate_workflow(definition))


@pytest.mark.asyncio
async def test_aftersales_review_cannot_start_as_a_manual_draft(monkeypatch):
    definition = graph()
    definition["nodes"][1]["data"].update({
        "after_approval_action": "create_aftersales_review",
        "case_category": "return_request",
    })
    monkeypatch.setattr(
        executions.WorkflowService, "get_by_id",
        AsyncMock(return_value=SimpleNamespace(definition=definition, version=1)),
    )
    monkeypatch.setattr(executions, "verify_approval_dependencies", AsyncMock())
    create = AsyncMock()
    monkeypatch.setattr(executions, "_create_execution_record", create)
    with pytest.raises(HTTPException) as error:
        await executions.execute_workflow(
            "flow", WorkflowExecuteRequest(inputs={}, async_mode=True),
            "project", AsyncMock(),
        )
    assert error.value.status_code == 403
    create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [{}, {"stream": True}])
async def test_approval_requires_background_execution_before_record_creation(monkeypatch, options):
    monkeypatch.setattr(
        executions.WorkflowService, "get_by_id",
        AsyncMock(return_value=SimpleNamespace(definition=graph(), version=1)),
    )
    create = AsyncMock()
    monkeypatch.setattr(executions, "_create_execution_record", create)
    with pytest.raises(HTTPException) as error:
        await executions.execute_workflow(
            "flow", WorkflowExecuteRequest(inputs={}, **options), "project", AsyncMock(),
        )
    assert error.value.status_code == 422
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_approval_cannot_run_on_to_answer_without_durable_pause():
    completed = []

    async def on_complete(**receipt):
        completed.append(receipt["node_id"])

    with pytest.raises(RuntimeError, match="审批"):
        await WorkflowExecutor(graph(), project_id="project", execution_id="run").run(
            {}, on_node_complete=on_complete,
        )
    assert "finish" not in completed


@pytest.mark.asyncio
async def test_approval_pauses_then_resumes_without_replaying_previous_nodes():
    completed = []

    async def on_complete(**receipt):
        completed.append(receipt["node_id"])

    async def persist(node_id, node_type, checkpoint):
        return node_type == "approval"

    definition = graph()
    with pytest.raises(WorkflowPaused) as paused:
        await WorkflowExecutor(
            definition, project_id="project", execution_id="run",
        ).run({}, on_node_complete=on_complete, on_checkpoint=persist)
    assert completed == ["start", "approval"]
    completed.clear()
    assert await WorkflowExecutor(
        definition, project_id="project", execution_id="run",
    ).run(
        {}, checkpoint=paused.value.checkpoint, on_node_complete=on_complete,
    ) == "确认后执行完成"
    assert completed == ["finish"]
