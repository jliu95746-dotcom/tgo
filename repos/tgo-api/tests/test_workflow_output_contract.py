"""Workflow proxy responses preserve JSON values, including plain answers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints.ai_workflows import (
    execute_workflow, get_execution, list_workflow_executions,
)
from app.schemas.ai_workflows import (
    NodeExecution, WorkflowExecuteRequest, WorkflowExecution,
    WorkflowSyncResponse,
)
from app.services.workflow_client import workflow_client


def record(output):
    return {
        "id": "execution-owned", "workflow_id": "workflow-owned",
        "status": "completed", "output": output,
        "started_at": "2026-09-08T00:00:00Z", "node_executions": [],
    }


def sync_response(output):
    return {
        "success": True, "output": output,
        "metadata": {"duration": 0.1, "startTime": "2026-09-08T00:00:00Z",
                     "endTime": "2026-09-08T00:00:01Z"},
    }


@pytest.mark.parametrize("output", [
    "这是文本答案", ["选项一", {"数量": 2}], 0, False, None, {"result": "答案"},
])
@pytest.mark.parametrize("kind", ["sync", "execution", "node"])
def test_all_response_shapes_preserve_json_output(kind, output):
    if kind == "sync":
        parsed = WorkflowSyncResponse.model_validate(sync_response(output))
    elif kind == "execution":
        parsed = WorkflowExecution.model_validate(record(output))
    else:
        parsed = NodeExecution.model_validate({
            **record(output), "node_id": "answer", "node_type": "answer",
            "execution_id": "execution-owned",
        })
    actual = parsed.model_dump(mode="json")["output"]
    assert actual == output and type(actual) is type(output)


def test_execution_response_keeps_nested_parent_coordinates():
    parsed = WorkflowExecution.model_validate({
        **record("完成"),
        "parent_execution_id": "parent-run",
        "workflow_lineage": ["workflow-a", "workflow-b"],
    })
    exposed = parsed.model_dump(mode="json")
    assert exposed["parent_execution_id"] == "parent-run"
    assert exposed["workflow_lineage"] == ["workflow-a", "workflow-b"]


def test_proxy_redacts_raw_inputs_and_legacy_intermediate_output():
    parsed = WorkflowExecution.model_validate({
        **record("客户可见答复"),
        "input": {"email": "private@example.test"},
        "node_executions": [{
            **record({"email": "legacy-private@example.test"}),
            "node_id": "input", "node_type": "input",
            "execution_id": "execution-owned",
            "input": {"email": "legacy-private@example.test"},
        }],
    })
    exposed = parsed.model_dump(mode="json")
    assert exposed["input"] == {"capture_status": "redacted", "field_count": 1}
    assert exposed["node_executions"][0]["input"] == {
        "capture_status": "redacted", "field_count": 1,
    }
    assert exposed["node_executions"][0]["output"] == {
        "capture_status": "redacted", "field_count": 1,
    }
    assert exposed["output"] == "客户可见答复"
    assert "private@example.test" not in str(exposed)


def test_proxy_keeps_only_valid_business_query_trace_coordinates():
    parsed = NodeExecution.model_validate({
        **record({
            "query_id": "2a1ab64c-8833-4cad-bc1e-799d26b2505a",
            "connection_id": "0ddba593-b4af-4855-a1c5-a64c2822be38",
            "operation": "order_query", "body": "private-provider-result",
        }),
        "node_id": "query", "node_type": "business_query",
        "execution_id": "execution-owned",
    })
    assert parsed.model_dump(mode="json")["output"] == {
        "query_id": "2a1ab64c-8833-4cad-bc1e-799d26b2505a",
        "connection_id": "0ddba593-b4af-4855-a1c5-a64c2822be38",
        "operation": "order_query",
    }


@pytest.mark.asyncio
async def test_public_endpoints_keep_text_outputs(monkeypatch):
    answer = "这款有红色的。"
    user = SimpleNamespace(project_id="project-owned")
    execute = AsyncMock(return_value=sync_response(answer))
    get = AsyncMock(return_value=record(answer))
    history = AsyncMock(return_value=[record(answer)])
    monkeypatch.setattr(workflow_client, "execute_workflow", execute)
    monkeypatch.setattr(workflow_client, "get_execution", get)
    monkeypatch.setattr(workflow_client, "list_workflow_executions", history)
    result = await execute_workflow(
        "workflow-owned", WorkflowExecuteRequest(inputs={}), user,
    )
    assert result.output == answer
    assert (await get_execution("execution-owned", user)).output == answer
    results = await list_workflow_executions("workflow-owned", 0, 20, user)
    assert results[0].output == answer
    assert execute.await_args.args[1] == "project-owned"
    assert get.await_args.args[1] == "project-owned"
    assert history.await_args.args[1] == "project-owned"
