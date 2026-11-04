"""LLM nodes must not run unreviewed tools through chat completions."""

from unittest.mock import AsyncMock

import pytest

from app.engine.context import ExecutionContext
from app.engine.executor import WorkflowExecutor
from app.engine.nodes.llm import LLMNodeExecutor
from app.integrations.llm_provider import LLMProvider
from app.services.node_readiness import node_configuration_errors


def test_llm_configuration_rejects_direct_tool_ids_without_approval() -> None:
    node = {
        "id": "model", "type": "llm", "data": {
            "reference_key": "model", "provider_id": "provider-1",
            "model_id": "model-1", "tool_ids": ["write-tool"],
        },
    }
    errors = node_configuration_errors([node])
    assert any("工具" in error for error in errors)


@pytest.mark.asyncio
async def test_saved_llm_node_rejects_tools_before_provider_call(monkeypatch) -> None:
    call = AsyncMock(return_value="should not run")
    monkeypatch.setattr(LLMProvider, "chat_completion", call)
    node = LLMNodeExecutor("model", {
        "data": {
            "provider_id": "provider-1", "model_id": "model-1",
            "user_prompt": "请继续", "tool_ids": ["write-tool"],
        },
    })
    with pytest.raises(ValueError, match="工具"):
        await node.execute(ExecutionContext({}, project_id="project-1"))
    call.assert_not_awaited()


@pytest.mark.asyncio
async def test_llm_provider_rejects_tools_before_chat_request(monkeypatch) -> None:
    from app.integrations.ai_client import AIClient

    call = AsyncMock()
    monkeypatch.setattr(AIClient, "chat_completions", call)
    with pytest.raises(ValueError, match="工具"):
        await LLMProvider.chat_completion(
            "provider-1", "model-1", "请继续",
            project_id="project-1", tool_ids=["write-tool"],
        )
    call.assert_not_awaited()


@pytest.mark.asyncio
async def test_saved_workflow_stops_before_answer_when_llm_has_tools(monkeypatch) -> None:
    call = AsyncMock(return_value="should not run")
    monkeypatch.setattr(LLMProvider, "chat_completion", call)
    graph = {
        "nodes": [
            {"id": "input", "type": "input", "data": {"reference_key": "input"}},
            {"id": "model", "type": "llm", "data": {
                "reference_key": "model", "provider_id": "provider-1",
                "model_id": "model-1", "user_prompt": "请继续",
                "tool_ids": ["write-tool"],
            }},
            {"id": "answer", "type": "answer", "data": {
                "reference_key": "answer", "output_type": "variable",
                "output_variable": "model.text",
            }},
        ],
        "edges": [
            {"source": "input", "target": "model"},
            {"source": "model", "target": "answer"},
        ],
    }
    completed = AsyncMock()
    with pytest.raises(RuntimeError, match="model"):
        await WorkflowExecutor(graph, project_id="project-1").run(
            {}, on_node_complete=completed,
        )
    assert [item.kwargs["node_id"] for item in completed.await_args_list] == [
        "input", "model",
    ]
    assert completed.await_args_list[-1].kwargs["status"] == "failed"
    call.assert_not_awaited()
