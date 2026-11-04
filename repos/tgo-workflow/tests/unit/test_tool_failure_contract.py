"""Tool failures stop the graph and never leak upstream secrets."""

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.engine.context import ExecutionContext
from app.engine.executor import WorkflowExecutor
from app.engine.nodes.tool import ToolNodeExecutor
from app.integrations.ai_client import AIClient
from app.integrations.http_client import HttpClient
from app.config import settings
from app.schemas.tool import ToolExecuteResponse
from pydantic import SecretStr


def tool_graph():
    return {
        "nodes": [
            {"id": "input", "type": "input", "data": {
                "reference_key": "input",
                "input_variables": [{"name": "order_no", "type": "string"}],
            }},
            {"id": "query", "type": "tool", "data": {
                "label": "订单查询", "reference_key": "query", "tool_id": "owned-tool",
                "input_mapping": {"order_no": "{{input.order_no}}"},
            }},
            {"id": "answer", "type": "answer", "data": {
                "reference_key": "answer", "output_type": "variable",
                "output_variable": "query.result",
            }},
        ],
        "edges": [
            {"source": "input", "target": "query"},
            {"source": "query", "target": "answer"},
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    ToolExecuteResponse(success=False, output_data=None, error="private-tool-token"),
    {"success": False, "error": "private-tool-token"},
    {"success": "true", "result": "invalid-contract"},
    OSError("private-upstream-url"),
        TimeoutError("private-timeout-url"),
])
async def test_failed_tool_stops_graph_without_downstream(monkeypatch, caplog, failure):
    invoke = AsyncMock(
        side_effect=failure if isinstance(failure, Exception) else None,
        return_value=failure if isinstance(failure, (dict, ToolExecuteResponse)) else None,
    )
    monkeypatch.setattr(AIClient, "execute_tool", invoke)
    complete = AsyncMock()
    with pytest.raises(RuntimeError, match="订单查询") as caught:
        await WorkflowExecutor(tool_graph(), project_id="owned-project").run(
            {"order_no": "owned-order"}, on_node_complete=complete,
        )
    assert [(call.kwargs["node_id"], call.kwargs["status"])
            for call in complete.await_args_list] == [
        ("input", "completed"), ("query", "failed"),
    ]
    assert "private-" not in str(caught.value) + caplog.text
    assert invoke.await_args.kwargs["project_id"] == "owned-project"


@pytest.mark.asyncio
async def test_successful_tool_preserves_typed_result_and_resolved_inputs(monkeypatch):
    invoke = AsyncMock(return_value=ToolExecuteResponse(
        success=True, output_data={"status": "shipped"},
    ))
    monkeypatch.setattr(AIClient, "execute_tool", invoke)
    result = await WorkflowExecutor(tool_graph(), project_id="owned-project").run(
        {"order_no": "owned-order"},
    )
    assert result == {"status": "shipped"}
    assert invoke.await_args.kwargs["inputs"] == {"order_no": "owned-order"}


@pytest.mark.asyncio
async def test_caller_input_cannot_supply_missing_project_scope(monkeypatch):
    invoke = AsyncMock(return_value=ToolExecuteResponse(
        success=True, output_data={"status": "shipped"},
    ))
    monkeypatch.setattr(AIClient, "execute_tool", invoke)
    with pytest.raises(RuntimeError, match="订单查询"):
        await WorkflowExecutor(tool_graph()).run({
            "order_no": "ORDER-1", "project_id": str(uuid4()),
        })
    invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["project", "tool"])
async def test_missing_scope_or_tool_never_makes_a_remote_call(monkeypatch, missing):
    invoke = AsyncMock()
    monkeypatch.setattr(AIClient, "execute_tool", invoke)
    data = tool_graph()["nodes"][1]
    if missing == "tool":
        data["data"].pop("tool_id")
    node = ToolNodeExecutor("query", data)
    context = ExecutionContext({}, project_id=None if missing == "project" else "owned-project")
    with pytest.raises(ValueError):
        await node.execute(context)
    invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_tool_http_failure_does_not_log_response_body(monkeypatch, caplog):
    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr("synthetic-tool-key"))
    response = httpx.Response(401, text="private-response-token")
    client = AsyncMock()
    client.post.return_value = response
    monkeypatch.setattr(HttpClient, "get_client", AsyncMock(return_value=client))
    with pytest.raises(RuntimeError):
        await AIClient.execute_tool(str(uuid4()), str(uuid4()), {})
    assert "private-response-token" not in caplog.text


@pytest.mark.asyncio
async def test_tool_client_uses_ai_direct_execution_contract_and_private_scope(monkeypatch):
    project_id, tool_id = str(uuid4()), str(uuid4())
    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr("synthetic-tool-key"))
    client = AsyncMock()
    client.post.return_value = httpx.Response(200, json={
        "success": True, "output_data": {"status": "shipped"}, "error": None,
    })
    monkeypatch.setattr(HttpClient, "get_client", AsyncMock(return_value=client))

    result = await AIClient.execute_tool(project_id, tool_id, {"order_no": "ORDER-1"})

    assert result == ToolExecuteResponse(success=True, output_data={"status": "shipped"})
    client.post.assert_awaited_once_with(
        f"{settings.AI_SERVICE_URL}/api/v1/tools/{tool_id}/execute",
        json={"input_data": {"order_no": "ORDER-1"}},
        headers={"X-Internal-API-Key": "synthetic-tool-key", "X-Project-Id": project_id,
                 "X-Workflow-Tool-Read-Only": "1"},
        timeout=60.0,
    )


@pytest.mark.asyncio
async def test_tool_client_fails_closed_without_private_identity(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", None)
    get_client = AsyncMock()
    monkeypatch.setattr(HttpClient, "get_client", get_client)

    with pytest.raises(RuntimeError, match="未配置"):
        await AIClient.execute_tool(str(uuid4()), str(uuid4()), {})
    get_client.assert_not_awaited()


@pytest.mark.asyncio
async def test_tool_client_rejects_legacy_result_shape_without_output_data(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr("synthetic-tool-key"))
    client = AsyncMock()
    client.post.return_value = httpx.Response(200, json={
        "success": True, "result": {"status": "shipped"},
    })
    monkeypatch.setattr(HttpClient, "get_client", AsyncMock(return_value=client))

    with pytest.raises(RuntimeError, match="暂时不可用"):
        await AIClient.execute_tool(str(uuid4()), str(uuid4()), {})


@pytest.mark.asyncio
async def test_tool_availability_client_checks_scope_and_fails_closed(monkeypatch):
    project_id, tool_id = str(uuid4()), str(uuid4())
    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr("synthetic-tool-key"))
    client = AsyncMock()
    client.get.return_value = httpx.Response(200, json={
        "project_id": project_id, "tool_id": tool_id,
        "available": True, "workflow_read_only": True,
    })
    monkeypatch.setattr(HttpClient, "get_client", AsyncMock(return_value=client))

    await AIClient.verify_tool_available(project_id, tool_id)
    client.get.assert_awaited_once_with(
        f"{settings.AI_SERVICE_URL}/api/v1/tools/{tool_id}/availability",
        headers={"X-Internal-API-Key": "synthetic-tool-key", "X-Project-Id": project_id},
        timeout=5.0,
    )

    client.get.return_value = httpx.Response(200, json={
        "project_id": str(uuid4()), "tool_id": tool_id,
        "available": True, "workflow_read_only": True,
    })
    with pytest.raises(PermissionError):
        await AIClient.verify_tool_available(project_id, tool_id)
    client.get.return_value = httpx.Response(200, json={
        "project_id": project_id, "tool_id": tool_id,
        "available": True, "workflow_read_only": False,
    })
    with pytest.raises(PermissionError):
        await AIClient.verify_tool_available(project_id, tool_id)
    client.get.return_value = httpx.Response(404, json={"detail": "private-data"})
    with pytest.raises(PermissionError):
        await AIClient.verify_tool_available(project_id, tool_id)
    client.get.side_effect = httpx.ConnectError("private-url")
    with pytest.raises(RuntimeError, match="无法核验"):
        await AIClient.verify_tool_available(project_id, tool_id)
