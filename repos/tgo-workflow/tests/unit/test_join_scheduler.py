"""A merge must wait for every branch that is still able to reach it."""

import pytest

from app.engine import executor as executor_module
from app.engine.checkpoint import WorkflowPaused
from app.engine.nodes.base import BaseNodeExecutor


class ValueNode(BaseNodeExecutor):
    async def execute(self, context):
        return {"value": self.config["value"]}, None


@pytest.fixture
def value_node(monkeypatch):
    original = executor_module.get_executor_class
    monkeypatch.setattr(
        executor_module,
        "get_executor_class",
        lambda kind: ValueNode if kind == "test_value" else original(kind),
    )


def node(node_id, kind, **data):
    return {"id": node_id, "type": kind, "data": {"reference_key": node_id, **data}}


def edge(source, target, handle=None):
    result = {"source": source, "target": target}
    if handle is not None:
        result["sourceHandle"] = handle
    return result


def unequal_branch_definition():
    return {
        "nodes": [
            node("start", "input", input_variables=[]),
            node("fast", "test_value", value="fast"),
            node("middle", "test_value", value="middle"),
            node("slow", "test_value", value="slow"),
            node("join", "answer", output_type="template",
                 output_template="{{fast.value}} + {{slow.value}}"),
        ],
        "edges": [
            edge("start", "fast"), edge("start", "middle"),
            edge("fast", "join"), edge("middle", "slow"), edge("slow", "join"),
        ],
    }


@pytest.mark.asyncio
async def test_merge_waits_for_a_longer_branch_and_runs_once(value_node):
    definition = unequal_branch_definition()
    started = []

    async def on_node_start(**event):
        started.append(event["node_id"])

    result = await executor_module.WorkflowExecutor(definition).run(
        {}, on_node_start=on_node_start,
    )
    assert result == "fast + slow"
    assert started.index("slow") < started.index("join")
    assert started.count("join") == 1


@pytest.mark.asyncio
async def test_resuming_an_unequal_branch_does_not_replay_or_jump_to_merge(value_node):
    definition = unequal_branch_definition()
    completed = []

    async def on_node_complete(**receipt):
        completed.append(receipt["node_id"])

    async def pause_after_fast(node_id, node_type, checkpoint):
        return node_id == "fast"

    with pytest.raises(WorkflowPaused) as paused:
        await executor_module.WorkflowExecutor(
            definition, project_id="project-1", execution_id="run-1",
        ).run({}, on_node_complete=on_node_complete, on_checkpoint=pause_after_fast)
    assert completed == ["start", "fast"]
    completed.clear()

    result = await executor_module.WorkflowExecutor(
        definition, project_id="project-1", execution_id="run-1",
    ).run({}, checkpoint=paused.value.checkpoint, on_node_complete=on_node_complete)
    assert result == "fast + slow"
    assert completed == ["middle", "slow", "join"]


@pytest.mark.asyncio
async def test_merge_does_not_wait_for_a_branch_rejected_by_condition(value_node):
    definition = {
        "nodes": [
            node("start", "input", input_variables=[{"name": "choice", "type": "string"}]),
            node("branch", "condition", condition_type="variable", variable="start.choice",
                 operator="equals", compare_value="yes"),
            node("yes", "test_value", value="chosen"),
            node("no", "test_value", value="not chosen"),
            node("join", "answer", output_type="template",
                 output_template="{{yes.value}}"),
        ],
        "edges": [
            edge("start", "branch"), edge("branch", "yes", "true"),
            edge("branch", "no", "false"), edge("yes", "join"), edge("no", "join"),
        ],
    }
    started = []

    async def on_node_start(**event):
        started.append(event["node_id"])

    result = await executor_module.WorkflowExecutor(definition).run(
        {"choice": "yes"}, on_node_start=on_node_start,
    )
    assert result == "chosen"
    assert "no" not in started
    assert started[-1] == "join"
