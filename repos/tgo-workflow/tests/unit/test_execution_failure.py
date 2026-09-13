"""A failed node must fail the run, not silently become a successful run."""

from unittest.mock import AsyncMock

import pytest

from app.engine.executor import WorkflowExecutor
from app.engine.context import ExecutionContext
from app.engine.nodes.condition import ConditionNodeExecutor


def graph(condition=None):
    return {
        "nodes": [
            {"id": "input", "type": "input", "data": {
                "label": "输入", "reference_key": "input",
                "input_variables": [{"name": "amount", "type": "number"}],
            }},
            {"id": "check", "type": "condition", "data": condition or {
                "label": "金额判断", "reference_key": "check",
                "condition_type": "variable", "variable": "input.amount",
                "operator": "greaterThan", "compare_value": "5",
            }},
            {"id": "answer", "type": "answer", "data": {
                "label": "回答", "reference_key": "answer",
                "output_type": "template", "output_template": "验证完成",
            }},
        ],
        "edges": [
            {"source": "input", "target": "check"},
            {"source": "check", "target": "answer", "sourceHandle": "true"},
        ],
    }


@pytest.mark.asyncio
async def test_failed_node_is_recorded_then_fails_run_without_downstream():
    complete = AsyncMock()
    with pytest.raises(RuntimeError, match="金额判断") as error:
        await WorkflowExecutor(graph()).run(
            {"amount": "private-input"}, on_node_complete=complete,
        )
    calls = [call.kwargs for call in complete.await_args_list]
    assert [(call["node_id"], call["status"]) for call in calls] == [
        ("input", "completed"), ("check", "failed"),
    ]
    assert "ValueError" in calls[-1]["error"]
    assert "private-input" not in calls[-1]["error"]
    assert "private-input" not in str(error.value)


@pytest.mark.asyncio
async def test_valid_branch_still_returns_answer():
    assert await WorkflowExecutor(graph()).run({"amount": 10}) == "验证完成"


@pytest.mark.asyncio
async def test_missing_runtime_node_fails_explicitly(monkeypatch):
    executor = WorkflowExecutor(graph())
    monkeypatch.setattr(
        executor.graph, "get_next_nodes", lambda *_: ["missing"],
    )
    with pytest.raises(ValueError, match="节点不存在"):
        await executor.run({"amount": 10})


@pytest.mark.asyncio
async def test_missing_executor_is_not_silently_skipped():
    definition = graph()
    definition["nodes"][1]["type"] = "unavailable-fixture-node"
    complete = AsyncMock()
    with pytest.raises(RuntimeError, match="不支持"):
        await WorkflowExecutor(definition).run(
            {"amount": 10}, on_node_complete=complete,
        )
    assert complete.await_args.kwargs["status"] == "failed"


@pytest.mark.asyncio
async def test_cycle_cannot_return_a_successful_empty_result():
    definition = graph()
    definition["edges"].append({"source": "answer", "target": "input"})
    with pytest.raises(ValueError):
        await WorkflowExecutor(definition).run({"amount": 10})


@pytest.mark.asyncio
async def test_invalid_expression_is_failure_not_false_branch():
    node = ConditionNodeExecutor("check", {"data": {
        "reference_key": "check", "condition_type": "expression",
        "expression": "missing_private_variable > 1",
    }})
    with pytest.raises(ValueError, match="表达式") as error:
        await node.execute(ExecutionContext({}))
    assert "missing_private_variable" not in str(error.value)


@pytest.mark.asyncio
async def test_valid_false_expression_is_still_a_false_branch():
    node = ConditionNodeExecutor("check", {"data": {
        "reference_key": "check", "condition_type": "expression",
        "expression": "input.amount > 5",
    }})
    assert await node.execute(ExecutionContext({"input.amount": 2})) == (
        {"result": False}, "false",
    )
