"""A workflow uses the single-agent contract and stops on a failed result."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.engine.executor import WorkflowExecutor
from app.integrations import ai_client
from app.integrations.http_client import HttpClient


def make_executor(definition, project_id, **options):
    options.setdefault("execution_id", str(uuid4()))
    return WorkflowExecutor(definition, project_id=project_id, **options)


def graph(agent):
    return {"nodes": [
        {"id": "input", "type": "input", "data": {"reference_key": "input", "input_variables": [{"name": "question", "type": "string", "required": True}]}},
        {"id": "agent", "type": "agent", "data": {"reference_key": "reply", "label": "商品咨询", "agent_id": agent, "input_mapping": {"message": "{{input.question}}"}}},
        {"id": "answer", "type": "answer", "data": {"reference_key": "answer", "output_type": "variable", "output_variable": "reply.text"}},
    ], "edges": [{"source": "input", "target": "agent"}, {"source": "agent", "target": "answer"}]}


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(ai_client, "settings", SimpleNamespace(AI_SERVICE_URL="http://owned-ai", API_INTERNAL_SERVICE_URL="http://owned-api", SECRET_KEY=SecretStr("synthetic-workflow-hop-key")))
    requests = []
    state = {"body": {
        "success": True, "content": "业务答复",
        "execution_id": str(uuid4()),
    }, "status": 200}

    def reply(request):
        requests.append(request)
        return httpx.Response(state["status"], json=state["body"])

    client = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    monkeypatch.setattr(HttpClient, "get_client", AsyncMock(return_value=client))
    return client, requests, state


@pytest.mark.asyncio
async def test_agent_node_sends_current_contract_with_internal_scope(transport):
    client, requests, _ = transport
    project, agent = str(uuid4()), str(uuid4())
    try:
        assert await make_executor(graph(agent), project_id=project).run({"question": "材质是什么？"}) == "业务答复"
        body = json.loads(requests[0].content)["payload"]
        assert body["agent_id"] == agent and "agent_ids" not in body
        assert body["knowledge_channel"] == "internal"
        assert body["require_current_knowledge"] is True
        assert requests[0].headers["X-Execution-Channel"] == "internal"
        assert requests[0].headers["X-Project-Id"] == project
        assert requests[0].headers["X-Internal-API-Key"] == "synthetic-workflow-hop-key"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_agent_node_forwards_fixed_device_and_policy(transport):
    client, requests, _ = transport
    project, agent, device = str(uuid4()), str(uuid4()), str(uuid4())
    definition = graph(agent)
    definition["nodes"][1]["data"].update({
        "expected_device_id": device,
        "expected_device_policy_digest": "a" * 64,
    })
    try:
        assert await make_executor(definition, project_id=project).run(
            {"question": "材质是什么？"}
        ) == "业务答复"
        body = json.loads(requests[0].content)["payload"]
        assert body["expected_device_id"] == device
        assert body["expected_device_policy_digest"] == "a" * 64
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_agent_node_forwards_parent_workflow_lineage(transport):
    client, requests, _ = transport
    project, agent, execution_id = str(uuid4()), str(uuid4()), str(uuid4())
    try:
        await make_executor(
            graph(agent), project_id=project,
            execution_id=execution_id, workflow_lineage=("workflow-a", "workflow-b"),
        ).run({"question": "材质是什么？"})
        body = json.loads(requests[0].content)
        assert body["execution_id"] == execution_id and body["node_id"] == "agent"
        assert "X-Workflow-Lineage" not in requests[0].headers
        assert requests[0].url == "http://owned-api/internal/workflow-ai/run"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_agent_node_records_child_execution_without_copying_answer(transport):
    client, _, state = transport
    project, agent, parent = str(uuid4()), str(uuid4()), str(uuid4())
    child = state["body"]["execution_id"]
    receipts = []

    async def capture(**event):
        receipts.append(event)

    try:
        result = await make_executor(
            graph(agent), project_id=project, execution_id=parent,
            workflow_lineage=("workflow-a",),
        ).run({"question": "材质是什么？"}, on_node_complete=capture)
        assert result == "业务答复"
        agent_receipt = next(row for row in receipts if row["node_id"] == "agent")
        assert agent_receipt["output"] == {
            "capture_status": "redacted", "field_count": 2,
            "agent_execution_id": child,
        }
        assert "业务答复" not in str(agent_receipt)
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("execution_id", [None, "not-a-uuid"])
async def test_agent_success_without_valid_execution_id_stops_workflow(
    transport, execution_id,
):
    client, _, state = transport
    state["body"]["execution_id"] = execution_id
    completed = AsyncMock()
    try:
        with pytest.raises(RuntimeError, match="agent"):
            await make_executor(
                graph(str(uuid4())), project_id=str(uuid4()),
            ).run({"question": "材质是什么？"}, on_node_complete=completed)
        assert [(row.kwargs["node_id"], row.kwargs["status"])
                for row in completed.await_args_list] == [
            ("input", "completed"), ("agent", "failed"),
        ]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_missing_internal_identity_never_dispatches_an_agent_request(transport, monkeypatch):
    client, requests, _ = transport
    monkeypatch.setattr(ai_client.settings, "SECRET_KEY", None)
    try:
        with pytest.raises(RuntimeError, match="商品咨询"):
            await make_executor(graph(str(uuid4())), project_id=str(uuid4())).run({"question": "test"})
        assert not requests
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("body,status", [
    ({"success": False, "content": "", "error": "private-upstream-token"}, 200),
    ({"success": "true", "content": "bad boolean"}, 200),
    ({"success": True, "content": "   "}, 200),
    ({"content": "missing success"}, 200),
    ({"credential": "private-upstream-token"}, 403),
])
async def test_invalid_agent_result_stops_following_answer_and_hides_private_details(transport, body, status, caplog):
    client, _, state = transport
    state.update(body=body, status=status)
    completed = AsyncMock()
    try:
        with pytest.raises(RuntimeError) as caught:
            await make_executor(graph(str(uuid4())), project_id=str(uuid4())).run({"question": "test"}, on_node_complete=completed)
        assert [(call.kwargs["node_id"], call.kwargs["status"]) for call in completed.await_args_list] == [("input", "completed"), ("agent", "failed")]
        assert "private-upstream-token" not in str(caught.value) + caplog.text
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_agent_exception_does_not_log_upstream_body(monkeypatch, caplog):
    marker = "private-customer-message-9071"
    monkeypatch.setattr(
        ai_client.AIClient, "run_agent",
        AsyncMock(side_effect=RuntimeError(marker)),
    )
    with pytest.raises(RuntimeError, match="agent"):
        await make_executor(
            graph(str(uuid4())), project_id=str(uuid4()),
        ).run({"question": "测试"})
    assert marker not in caplog.text


@pytest.mark.asyncio
async def test_ai_http_error_does_not_log_private_response(transport, caplog):
    client, _, state = transport
    marker = "private-upstream-response-9071"
    state.update(status=403, body={"detail": marker})
    try:
        with pytest.raises(Exception, match="HTTP|status"):
            await ai_client.AIClient.chat_completions(str(uuid4()), {})
        assert marker not in caplog.text
        assert "HTTP 403" in caplog.text
    finally:
        await client.aclose()
