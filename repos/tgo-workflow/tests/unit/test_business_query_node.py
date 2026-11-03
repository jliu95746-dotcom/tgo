"""A governed customer query uses only a live private session and bounded receipts."""

from unittest.mock import AsyncMock
from uuid import uuid4
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from fastapi import HTTPException

from app.engine.context import ExecutionContext
from app.engine.executor import WorkflowExecutor
from app.engine.nodes.business_query import BusinessQueryNodeExecutor
from app.schemas.business_query import (
    BusinessExecutionContext, BusinessQueryCommand, BusinessQueryResult,
)
from app.schemas.execution import WorkflowExecutionOrigin
from app.schemas.execution import WorkflowExecuteRequest
from app.services.business_query_context import (
    business_query_record_inputs, require_business_query_context,
)
from app.services.validation_service import ValidationService


def definition(connection_id: str) -> dict:
    return {
        "nodes": [
            {"id": "start", "type": "input", "data": {
                "reference_key": "input", "input_variables": [
                    {"name": "order_no", "type": "string", "required": True},
                ],
            }},
            {"id": "query", "type": "business_query", "data": {
                "label": "查询订单", "reference_key": "order",
                "connection_id": connection_id, "operation": "order_query",
                "order_variable": "input.order_no",
            }},
            {"id": "answer", "type": "answer", "data": {
                "reference_key": "answer", "output_type": "variable",
                "output_variable": "order.status",
            }},
        ],
        "edges": [
            {"source": "start", "target": "query"},
            {"source": "query", "target": "answer"},
        ],
    }


def context() -> BusinessExecutionContext:
    return BusinessExecutionContext(
        project_id=uuid4(), request_id=uuid4(), visitor_session_id=uuid4(),
        visitor_id=uuid4(), request_fingerprint="a" * 64, channel="web",
        context_token=SecretStr("private-test-context"),
    )


def test_private_context_must_match_verified_origin():
    private = context()
    origin = WorkflowExecutionOrigin(
        ai_request_id="ai-run-a", business_project_id=private.project_id,
        business_request_id=private.request_id,
        visitor_session_id=private.visitor_session_id,
    )
    args = {
        "context_token": private.context_token.get_secret_value(),
        "visitor_id": str(private.visitor_id),
        "request_fingerprint": private.request_fingerprint,
        "channel": private.channel,
    }
    checked = require_business_query_context(str(private.project_id), origin, **args)
    assert checked == private
    assert "private-test-context" not in str(checked.model_dump())
    with pytest.raises(PermissionError):
        require_business_query_context(str(uuid4()), origin, **args)
    with pytest.raises(PermissionError):
        require_business_query_context(str(private.project_id), origin,
                                       **{**args, "context_token": None})


def test_order_number_is_not_persisted_in_execution_inputs():
    saved = business_query_record_inputs({"order_no": "ORDER-1234"})
    assert saved == {"order_no": "[REDACTED]"}
    with pytest.raises(ValueError):
        business_query_record_inputs({"order_no": "ORDER-1234", "extra": "private"})


def test_query_definition_requires_governed_connection_and_input():
    graph = definition(str(uuid4()))
    assert ValidationService.validate_workflow(graph) == []
    graph["nodes"][1]["data"]["connection_id"] = "https://foreign.example.test"
    assert any("connection_id" in issue for issue in ValidationService.validate_workflow(graph))
    graph["nodes"][1]["data"]["connection_id"] = str(uuid4())
    graph["nodes"][1]["data"]["order_variable"] = "input.other"
    assert any("order_variable" in issue for issue in ValidationService.validate_workflow(graph))


def test_query_definition_does_not_leak_order_to_other_nodes():
    graph = definition(str(uuid4()))
    graph["nodes"][2]["data"]["output_variable"] = "input.order_no"
    assert any("订单号" in issue for issue in ValidationService.validate_workflow(graph))
    graph["nodes"][2]["data"]["output_variable"] = "order.status"
    graph["nodes"][0]["data"]["input_variables"].append(
        {"name": "private_note", "type": "string"},
    )
    assert any("只允许一个" in issue for issue in ValidationService.validate_workflow(graph))


@pytest.mark.parametrize("condition_data", [
    {"condition_type": "llm", "llm_prompt": "判断 {{input.order_no}} 是否已发货"},
    {"condition_type": "expression", "expression": "input.order_no == 'ORDER-1234'"},
    {"condition_type": "variable", "variable": "input.order_no",
     "operator": "equals", "compare_value": "ORDER-1234"},
    {"condition_type": "variable", "variable": "order.outcome",
     "operator": "equals", "compare_value": "{{input.order_no}}"},
])
def test_governed_query_rejects_unsafe_conditions(condition_data):
    graph = definition(str(uuid4()))
    graph["nodes"].insert(2, {"id": "check", "type": "condition", "data": {
        "label": "检查查询结果", "reference_key": "check", **condition_data,
    }})
    graph["edges"] = [
        {"source": "start", "target": "query"},
        {"source": "query", "target": "check"},
        {"source": "check", "target": "answer", "sourceHandle": "true"},
    ]
    assert any("受控业务查询条件" in issue
               for issue in ValidationService.validate_workflow(graph))


@pytest.mark.asyncio
async def test_governed_query_blocks_model_condition_before_any_node(monkeypatch):
    from app.integrations.ai_client import AIClient
    from app.integrations.llm_provider import LLMProvider

    graph = definition(str(uuid4()))
    graph["nodes"].insert(2, {"id": "check", "type": "condition", "data": {
        "label": "模型判断", "reference_key": "check", "condition_type": "llm",
        "llm_prompt": "判断 {{input.order_no}} 是否已发货",
    }})
    graph["edges"] = [
        {"source": "start", "target": "query"},
        {"source": "query", "target": "check"},
        {"source": "check", "target": "answer", "sourceHandle": "true"},
    ]
    query = AsyncMock()
    model = AsyncMock()
    monkeypatch.setattr(AIClient, "query_business", query)
    monkeypatch.setattr(LLMProvider, "chat_completion", model)
    private = context()
    with pytest.raises(ValueError, match="受控业务查询条件"):
        await WorkflowExecutor(
            graph, project_id=str(private.project_id), execution_id=str(uuid4()),
            employee_agent_id=str(uuid4()), business_context=private,
        ).run({"order_no": "ORDER-1234"})
    query.assert_not_awaited()
    model.assert_not_awaited()


@pytest.mark.asyncio
async def test_business_query_requires_live_context_before_network(monkeypatch):
    from app.integrations.ai_client import AIClient

    query = AsyncMock()
    monkeypatch.setattr(AIClient, "query_business", query)
    graph = definition(str(uuid4()))
    node = BusinessQueryNodeExecutor("query", graph["nodes"][1])
    runtime = ExecutionContext({"input.order_no": "ORDER-1234"},
                               project_id=str(uuid4()), execution_id=str(uuid4()))
    with pytest.raises(PermissionError):
        await node.execute(runtime)
    query.assert_not_awaited()


@pytest.mark.asyncio
async def test_business_query_exposes_only_normalized_result(monkeypatch):
    from app.integrations.ai_client import AIClient

    private_context = context()
    execution_id = uuid4()
    agent_id = uuid4()
    connection_id = uuid4()
    query_id = uuid4()
    query = AsyncMock(return_value=BusinessQueryResult(
        project_id=private_context.project_id,
        workflow_execution_id=execution_id, query_id=query_id,
        connection_id=connection_id, operation="order_query", outcome="passed",
        summary={"status": "shipped", "carrier": "测试快递",
                 "tracking_no_masked": "1234****5678"}, error_code=None,
    ))
    monkeypatch.setattr(AIClient, "query_business", query)
    graph = definition(str(connection_id))
    node = BusinessQueryNodeExecutor("query", graph["nodes"][1])
    runtime = ExecutionContext(
        {"input.order_no": "ORDER-1234"}, project_id=str(private_context.project_id),
        execution_id=str(execution_id), employee_agent_id=str(agent_id),
        business_context=private_context,
    )
    outputs, handle = await node.execute(runtime)
    assert handle is None
    assert outputs == {
        "outcome": "passed", "status": "shipped", "carrier": "测试快递",
        "tracking_no_masked": "1234****5678", "error_code": None,
        "query_id": str(query_id),
        "connection_id": str(connection_id), "operation": "order_query",
    }
    assert "ORDER-1234" not in str(outputs)
    assert "private-test-context" not in str(outputs)
    query.assert_awaited_once()


@pytest.mark.asyncio
async def test_published_query_definition_returns_only_normalized_status(monkeypatch):
    from app.integrations.ai_client import AIClient

    private = context()
    execution_id, agent_id, connection_id = uuid4(), uuid4(), uuid4()
    monkeypatch.setattr(AIClient, "query_business", AsyncMock(return_value=BusinessQueryResult(
        project_id=private.project_id, workflow_execution_id=execution_id,
        query_id=uuid4(), connection_id=connection_id, operation="order_query",
        outcome="passed", summary={"status": "shipped"}, error_code=None,
    )))
    recorded = []

    async def capture_node(**event):
        recorded.append(event)

    result = await WorkflowExecutor(
        definition(str(connection_id)), project_id=str(private.project_id),
        execution_id=str(execution_id), employee_agent_id=str(agent_id),
        business_context=private,
    ).run({"order_no": "ORDER-1234"}, on_node_complete=capture_node)
    assert result == "shipped"
    assert recorded[0]["node_type"] == "input"
    assert recorded[0]["output"] == {"order_no": "[REDACTED]"}
    query_receipt = next(item for item in recorded if item["node_type"] == "business_query")
    assert query_receipt["output"]["connection_id"] == str(connection_id)
    assert query_receipt["output"]["operation"] == "order_query"
    assert "ORDER-1234" not in str(recorded)
    assert "private-test-context" not in str(recorded)


@pytest.mark.asyncio
async def test_governed_query_never_runs_answer_that_exposes_order(monkeypatch):
    from app.integrations.ai_client import AIClient

    graph = definition(str(uuid4()))
    graph["nodes"][2]["data"]["output_variable"] = "input.order_no"
    query = AsyncMock()
    monkeypatch.setattr(AIClient, "query_business", query)
    private = context()
    with pytest.raises(ValueError, match="订单号"):
        await WorkflowExecutor(
            graph, project_id=str(private.project_id), execution_id=str(uuid4()),
            employee_agent_id=str(uuid4()), business_context=private,
        ).run({"order_no": "ORDER-1234"})
    query.assert_not_awaited()


@pytest.mark.asyncio
async def test_governed_query_never_persists_raw_context_to_checkpoint(monkeypatch):
    from app.integrations.ai_client import AIClient

    query = AsyncMock()
    monkeypatch.setattr(AIClient, "query_business", query)
    private = context()
    on_checkpoint = AsyncMock(return_value=False)
    with pytest.raises(ValueError, match="检查点"):
        await WorkflowExecutor(
            definition(str(uuid4())), project_id=str(private.project_id),
            execution_id=str(uuid4()), employee_agent_id=str(uuid4()),
            business_context=private,
        ).run({"order_no": "ORDER-1234"}, on_checkpoint=on_checkpoint)
    query.assert_not_awaited()
    on_checkpoint.assert_not_awaited()


@pytest.mark.asyncio
async def test_editor_cannot_execute_governed_query(monkeypatch):
    from app.api import executions

    private_project = str(uuid4())
    workflow = SimpleNamespace(
        definition=definition(str(uuid4())), version=1, status="draft",
    )
    monkeypatch.setattr(
        executions.WorkflowService, "get_by_id", AsyncMock(return_value=workflow),
    )
    create_record = AsyncMock()
    monkeypatch.setattr(executions, "_create_execution_record", create_record)
    with pytest.raises(HTTPException) as caught:
        await executions.execute_workflow(
            "workflow-a", WorkflowExecuteRequest(inputs={"order_no": "ORDER-1234"}),
            project_id=private_project, db=AsyncMock(), x_internal_api_key=None,
        )
    assert caught.value.status_code == 403
    create_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_governed_query_refuses_worker_queue(monkeypatch):
    from app.api import executions

    private_project = str(uuid4())
    workflow = SimpleNamespace(
        definition=definition(str(uuid4())), version=1, status="active",
    )
    monkeypatch.setattr(
        executions.WorkflowService, "get_by_id", AsyncMock(return_value=workflow),
    )
    monkeypatch.setattr(
        executions, "production_definition", AsyncMock(return_value=SimpleNamespace(
            definition=workflow.definition, version=1, release_id="release-a",
        )),
    )
    monkeypatch.setattr(executions, "verify_execution_binding", lambda *_args: None)
    create_record = AsyncMock()
    monkeypatch.setattr(executions, "_create_execution_record", create_record)
    with pytest.raises(HTTPException) as caught:
        await executions.execute_workflow(
            "workflow-a", WorkflowExecuteRequest(
                inputs={"order_no": "ORDER-1234"}, async_mode=True,
                employee_agent_id=uuid4(), execution_mode="production",
                release_version=1, expected_release_id="release-a",
                expected_release_digest="a" * 64,
            ), project_id=private_project, db=AsyncMock(), x_internal_api_key=None,
        )
    assert caught.value.status_code == 422
    create_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_private_client_rejects_foreign_receipt_without_exposing_request(monkeypatch):
    from app.config import settings
    from app.integrations.ai_client import AIClient
    from app.integrations.http_client import HttpClient

    private = context()
    command = BusinessQueryCommand(
        project_id=private.project_id, agent_id=uuid4(),
        workflow_execution_id=uuid4(), connection_id=uuid4(),
        operation="order_query", visitor_session_id=private.visitor_session_id,
        order_no=SecretStr("ORDER-1234"), context=private,
    )
    response = SimpleNamespace(status_code=200, json=lambda: BusinessQueryResult(
        project_id=uuid4(), workflow_execution_id=command.workflow_execution_id,
        query_id=uuid4(), connection_id=command.connection_id,
        operation="order_query", outcome="passed",
        summary={"status": "shipped"}, error_code=None,
    ).model_dump(mode="json"))
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    monkeypatch.setattr(HttpClient, "get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr("test-internal-key"))
    with pytest.raises(RuntimeError, match="范围不一致") as caught:
        await AIClient.query_business(command)
    assert "ORDER-1234" not in str(caught.value)
    assert "private-test-context" not in str(caught.value)
    sent = client.post.call_args.kwargs
    assert sent["headers"]["X-Project-Id"] == str(private.project_id)
    assert sent["json"]["order_no"] == "ORDER-1234"
