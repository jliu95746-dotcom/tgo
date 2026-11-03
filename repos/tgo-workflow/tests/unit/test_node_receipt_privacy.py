"""Execution receipts keep shape evidence without copying private node data."""

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.engine.executor import WorkflowExecutor
from app.engine.nodes import api as api_node
from app.integrations.ai_client import AIClient
from app.integrations.llm_provider import LLMProvider
from app.schemas.tool import ToolExecuteResponse
from app.services.execution_receipts import node_receipt_output


def test_agent_receipt_keeps_only_valid_child_execution_identity():
    execution_id = str(uuid4())
    assert node_receipt_output("agent", {
        "text": "private-answer", "agent_execution_id": execution_id,
    }, governed_query=False) == {
        "capture_status": "redacted", "field_count": 2,
        "agent_execution_id": execution_id,
    }
    assert node_receipt_output("agent", {
        "text": "private-answer", "agent_execution_id": "private-token",
    }, governed_query=False) == {
        "capture_status": "redacted", "field_count": 2,
    }


def tool_graph():
    return {
        "nodes": [
            {"id": "input", "type": "input", "data": {
                "reference_key": "input",
                "input_variables": [{"name": "question", "type": "string"}],
            }},
            {"id": "tool", "type": "tool", "data": {
                "reference_key": "tool", "tool_id": "owned-tool",
                "input_mapping": {"question": "{{input.question}}"},
            }},
            {"id": "answer", "type": "answer", "data": {
                "reference_key": "answer", "output_type": "template",
                "output_template": "查询完成",
            }},
        ],
        "edges": [
            {"source": "input", "target": "tool"},
            {"source": "tool", "target": "answer"},
        ],
    }


@pytest.mark.asyncio
async def test_node_receipts_redact_input_and_tool_result_but_keep_runtime_values(monkeypatch):
    private_question = "customer-private-question"
    private_result = "provider-private-result"
    invoke = AsyncMock(return_value=ToolExecuteResponse(
        success=True, output_data={"result": private_result},
    ))
    monkeypatch.setattr(AIClient, "execute_tool", invoke)
    receipts = []

    async def capture(**event):
        receipts.append(event)

    result = await WorkflowExecutor(tool_graph(), project_id="owned-project").run(
        {"question": private_question}, on_node_complete=capture,
    )

    assert result == "查询完成"
    assert invoke.await_args.kwargs["inputs"] == {"question": private_question}
    assert receipts[0]["output"] == {"capture_status": "redacted", "field_count": 1}
    assert receipts[1]["output"] == {"capture_status": "redacted", "field_count": 1}
    assert receipts[2]["output"] == {"result": "查询完成"}
    assert private_question not in str(receipts)
    assert private_result not in str(receipts)


@pytest.mark.asyncio
async def test_api_response_body_is_used_in_context_but_not_copied_to_receipt(monkeypatch):
    private_response = "provider-private-token"
    query = AsyncMock(return_value=httpx.Response(
        200, json={"value": private_response},
        request=httpx.Request("GET", "https://example.test/query"),
    ))
    monkeypatch.setattr(api_node, "bounded_api_get", query)
    graph = {
        "nodes": [
            {"id": "input", "type": "input", "data": {
                "reference_key": "input", "input_variables": [],
            }},
            {"id": "api", "type": "api", "data": {
                "reference_key": "api", "method": "GET",
                "url": "https://example.test/query", "body_type": "none",
            }},
            {"id": "answer", "type": "answer", "data": {
                "reference_key": "answer", "output_type": "template",
                "output_template": "查询完成",
            }},
        ],
        "edges": [
            {"source": "input", "target": "api"},
            {"source": "api", "target": "answer"},
        ],
    }
    receipts = []

    async def capture(**event):
        receipts.append(event)

    result = await WorkflowExecutor(graph).run({}, on_node_complete=capture)

    assert result == "查询完成"
    assert query.await_count == 1
    assert receipts[1]["output"] == {
        "capture_status": "redacted", "field_count": 3, "status_code": 200,
    }
    assert private_response not in str(receipts)


@pytest.mark.asyncio
async def test_llm_intermediate_text_is_not_saved_in_node_receipt(monkeypatch):
    private_text = "model-private-customer-detail"
    chat = AsyncMock(return_value=private_text)
    monkeypatch.setattr(LLMProvider, "chat_completion", chat)
    graph = {
        "nodes": [
            {"id": "input", "type": "input", "data": {
                "reference_key": "input", "input_variables": [],
            }},
            {"id": "llm", "type": "llm", "data": {
                "reference_key": "llm", "user_prompt": "请分类",
            }},
            {"id": "answer", "type": "answer", "data": {
                "reference_key": "answer", "output_type": "template",
                "output_template": "已处理",
            }},
        ],
        "edges": [
            {"source": "input", "target": "llm"},
            {"source": "llm", "target": "answer"},
        ],
    }
    receipts = []

    async def capture(**event):
        receipts.append(event)

    assert await WorkflowExecutor(graph).run({}, on_node_complete=capture) == "已处理"
    chat.assert_awaited_once()
    assert receipts[1]["output"] == {"capture_status": "redacted", "field_count": 1}
    assert private_text not in str(receipts)
