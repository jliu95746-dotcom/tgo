"""Parallel read-only branches overlap and join without sharing mutable state."""

import asyncio
from datetime import datetime, timezone

import pytest

from app.engine.checkpoint import WorkflowPaused
from app.engine.executor import WorkflowExecutor, WorkflowNodeFailure
from app.engine.nodes.llm import LLMNodeExecutor
from app.services.execution_lifecycle import ExecutionStopped
from app.services.validation_service import ValidationService


def definition(*, wait_for_all=True, timeout=2):
    return {
        "nodes": [
            {"id": "start", "type": "input", "data": {
                "reference_key": "input", "input_variables": [],
            }},
            {"id": "fork", "type": "parallel", "data": {
                "reference_key": "fork", "branches": 2,
                "wait_for_all": wait_for_all, "timeout": timeout,
            }},
            {"id": "first", "type": "llm", "data": {
                "reference_key": "first", "provider_id": "provider", "model_id": "model",
                "user_prompt": "first",
            }},
            {"id": "second", "type": "llm", "data": {
                "reference_key": "second", "provider_id": "provider", "model_id": "model",
                "user_prompt": "second",
            }},
            {"id": "join", "type": "answer", "data": {
                "reference_key": "join", "output_type": "template",
                "output_template": "{{first.text}} + {{second.text}}",
            }},
        ],
        "edges": [
            {"source": "start", "target": "fork"},
            {"source": "fork", "target": "first", "sourceHandle": "branch-0"},
            {"source": "fork", "target": "second", "sourceHandle": "branch-1"},
            {"source": "first", "target": "join"},
            {"source": "second", "target": "join"},
        ],
    }


def multi_step_definition():
    graph = definition()
    graph["nodes"].insert(3, {"id": "followup", "type": "llm", "data": {
        "reference_key": "followup", "provider_id": "provider", "model_id": "model",
        "user_prompt": "{{first.text}}",
    }})
    graph["edges"][3]["target"] = "followup"
    graph["edges"].append({"source": "followup", "target": "join"})
    graph["nodes"][-1]["data"]["output_template"] = "{{followup.text}} + {{second.text}}"
    return graph


def test_parallel_accepts_independent_multi_step_read_only_branches():
    assert ValidationService.validate_workflow(multi_step_definition()) == []


@pytest.mark.parametrize("change", [
    lambda graph: graph["nodes"][3]["data"].update(user_prompt="{{second.text}}"),
    lambda graph: graph["nodes"][2]["data"].update(user_prompt="{{followup.text}}"),
    lambda graph: graph["nodes"][3].update(type="tool", data={
        "reference_key": "followup", "tool_id": "write-tool",
    }),
])
def test_multi_step_parallel_rejects_cross_branch_future_and_effectful_nodes(change):
    graph = multi_step_definition()
    change(graph)
    assert ValidationService.validate_workflow(graph)


@pytest.mark.asyncio
async def test_multi_step_branches_overlap_and_read_prior_branch_result(monkeypatch):
    second_started = asyncio.Event()
    followup_started = asyncio.Event()
    calls = []

    async def execute(self, context):
        calls.append(self.node_id)
        if self.node_id == "first":
            await asyncio.wait_for(second_started.wait(), 1)
            return {"text": "first"}, None
        if self.node_id == "followup":
            assert context.get_variable("first.text") == "first"
            followup_started.set()
            return {"text": "first-followup"}, None
        second_started.set()
        await asyncio.wait_for(followup_started.wait(), 1)
        assert context.get_variable("first.text") is None
        return {"text": "second"}, None

    monkeypatch.setattr(LLMNodeExecutor, "execute", execute)
    result = await WorkflowExecutor(multi_step_definition()).run({})
    assert result == "first-followup + second"
    assert calls.index("first") < calls.index("followup")
    assert calls.index("second") < calls.index("followup")


@pytest.mark.asyncio
async def test_later_branch_step_failure_cancels_other_branch_and_skips_join(monkeypatch):
    second_started = asyncio.Event()
    second_cancelled = asyncio.Event()
    receipts = []

    async def execute(self, context):
        if self.node_id == "first":
            await second_started.wait()
            return {"text": "first"}, None
        if self.node_id == "followup":
            raise RuntimeError("private provider response")
        second_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            second_cancelled.set()
            raise

    async def on_node_complete(**receipt):
        receipts.append(receipt)

    monkeypatch.setattr(LLMNodeExecutor, "execute", execute)
    with pytest.raises(WorkflowNodeFailure) as error:
        await WorkflowExecutor(multi_step_definition()).run(
            {}, on_node_complete=on_node_complete,
        )
    assert "private provider response" not in str(error.value)
    assert second_cancelled.is_set()
    assert not any(receipt["node_id"] == "join" for receipt in receipts)
    assert any(
        receipt["node_id"] == "followup" and receipt["status"] == "failed"
        for receipt in receipts
    )


@pytest.mark.asyncio
async def test_worker_stop_callback_propagates_without_turning_run_into_failure(monkeypatch):
    second_started = asyncio.Event()
    second_cancelled = asyncio.Event()
    receipts = []

    async def execute(self, context):
        if self.node_id == "first":
            await second_started.wait()
            return {"text": "first"}, None
        second_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            second_cancelled.set()
            raise

    async def on_node_complete(**receipt):
        receipts.append(receipt)
        if receipt["node_id"] == "first":
            raise ExecutionStopped("user stopped the run")

    monkeypatch.setattr(LLMNodeExecutor, "execute", execute)
    with pytest.raises(ExecutionStopped):
        await WorkflowExecutor(definition()).run(
            {}, on_node_complete=on_node_complete,
        )
    assert second_cancelled.is_set()
    assert not any(receipt["node_id"] in {"fork", "join"} for receipt in receipts)


@pytest.mark.asyncio
async def test_checkpoint_after_multi_step_group_does_not_replay_steps(monkeypatch):
    calls = []

    async def execute(self, context):
        calls.append(self.node_id)
        return {"text": self.node_id}, None

    async def pause_after_fork(node_id, node_type, checkpoint):
        return node_id == "fork"

    monkeypatch.setattr(LLMNodeExecutor, "execute", execute)
    graph = multi_step_definition()
    executor = WorkflowExecutor(graph, project_id="project", execution_id="run")
    with pytest.raises(WorkflowPaused) as paused:
        await executor.run({}, on_checkpoint=pause_after_fork)
    assert set(paused.value.checkpoint.completed_nodes) == {
        "start", "fork", "first", "followup", "second",
    }
    resumed = WorkflowExecutor(graph, project_id="project", execution_id="run")
    assert await resumed.run({}, checkpoint=paused.value.checkpoint) == "followup + second"
    assert sorted(calls) == ["first", "followup", "second"]


def test_parallel_topology_accepts_two_read_only_branches():
    assert ValidationService.validate_workflow(definition()) == []


@pytest.mark.parametrize("change", [
    lambda graph: graph["nodes"][1]["data"].update(wait_for_all=False),
    lambda graph: graph["nodes"][1]["data"].update(timeout=0),
    lambda graph: graph["edges"][2].update(sourceHandle="branch-0"),
    lambda graph: graph["edges"][2].update(sourceHandle=[]),
    lambda graph: graph["edges"].append({"source": "first", "target": "second"}),
    lambda graph: graph["nodes"][2].update(type="tool", data={
        "reference_key": "first", "tool_id": "side-effect-tool",
    }),
])
def test_parallel_rejects_ambiguous_or_effectful_graphs(change):
    graph = definition()
    change(graph)
    assert ValidationService.validate_workflow(graph)


@pytest.mark.asyncio
async def test_branches_overlap_and_merge_in_configured_order(monkeypatch):
    both_started = asyncio.Event()
    started = set()
    receipts = []

    async def execute(self, context):
        started.add(self.node_id)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), 0.5)
        await asyncio.sleep(0.02)
        return {"text": self.node_id}, None

    async def on_node_complete(**receipt):
        receipts.append(receipt)

    monkeypatch.setattr(LLMNodeExecutor, "execute", execute)
    result = await WorkflowExecutor(definition()).run(
        {}, on_node_complete=on_node_complete,
    )
    assert result == "first + second"
    assert {item["node_id"] for item in receipts if item["status"] == "completed"} == {
        "start", "fork", "first", "second", "join",
    }
    assert all(item["duration"] >= 0 for item in receipts)
    branches = [item for item in receipts if item["node_id"] in {"first", "second"}]
    assert all(isinstance(item["started_at"], datetime) for item in branches)
    assert all(isinstance(item["completed_at"], datetime) for item in branches)
    assert max(item["started_at"] for item in branches) < min(
        item["completed_at"] for item in branches
    )
    for item in branches:
        elapsed_ms = (item["completed_at"] - item["started_at"]).total_seconds() * 1000
        assert abs(item["duration"] - elapsed_ms) < 20
        assert item["completed_at"] <= datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_branch_failure_cancels_sibling_and_never_reaches_join(monkeypatch):
    sibling_started = asyncio.Event()
    sibling_cancelled = asyncio.Event()
    receipts = []

    async def execute(self, context):
        if self.node_id == "first":
            await sibling_started.wait()
            raise ValueError("private provider response")
        sibling_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            sibling_cancelled.set()
            raise

    async def on_node_complete(**receipt):
        receipts.append(receipt)

    monkeypatch.setattr(LLMNodeExecutor, "execute", execute)
    with pytest.raises(WorkflowNodeFailure) as error:
        await WorkflowExecutor(definition()).run(
            {}, on_node_complete=on_node_complete,
        )
    assert "private provider response" not in str(error.value)
    assert sibling_cancelled.is_set()
    assert not any(item["node_id"] == "join" for item in receipts)
    assert any(item["node_id"] == "first" and item["status"] == "failed" for item in receipts)


@pytest.mark.asyncio
async def test_group_timeout_cancels_both_branches(monkeypatch):
    cancelled = set()

    async def execute(self, context):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.add(self.node_id)
            raise

    monkeypatch.setattr(LLMNodeExecutor, "execute", execute)
    with pytest.raises(WorkflowNodeFailure):
        await WorkflowExecutor(definition(timeout=1)).run({})
    assert cancelled == {"first", "second"}


@pytest.mark.asyncio
async def test_external_cancellation_stops_both_branches(monkeypatch):
    both_started = asyncio.Event()
    started = set()
    cancelled = set()

    async def execute(self, context):
        started.add(self.node_id)
        if len(started) == 2:
            both_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.add(self.node_id)
            raise

    async def on_node_complete(**receipt):
        if receipt["status"] == "cancelled":
            raise RuntimeError("run already stopped")

    monkeypatch.setattr(LLMNodeExecutor, "execute", execute)
    running = asyncio.create_task(WorkflowExecutor(definition()).run(
        {}, on_node_complete=on_node_complete,
    ))
    await asyncio.wait_for(both_started.wait(), 0.5)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert cancelled == {"first", "second"}


@pytest.mark.asyncio
async def test_shared_callbacks_are_serialized_while_branches_overlap(monkeypatch):
    both_started = asyncio.Event()
    active_callbacks = 0
    highest_concurrent_callbacks = 0
    started = set()

    async def execute(self, context):
        started.add(self.node_id)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), 0.5)
        return {"text": self.node_id}, None

    async def on_node_complete(**receipt):
        nonlocal active_callbacks, highest_concurrent_callbacks
        active_callbacks += 1
        highest_concurrent_callbacks = max(highest_concurrent_callbacks, active_callbacks)
        await asyncio.sleep(0.01)
        active_callbacks -= 1

    monkeypatch.setattr(LLMNodeExecutor, "execute", execute)
    await WorkflowExecutor(definition()).run({}, on_node_complete=on_node_complete)
    assert highest_concurrent_callbacks == 1


@pytest.mark.asyncio
async def test_checkpoint_after_group_never_replays_branches(monkeypatch):
    calls = []

    async def execute(self, context):
        calls.append(self.node_id)
        return {"text": self.node_id}, None

    async def pause_after_fork(node_id, node_type, checkpoint):
        return node_id == "fork"

    monkeypatch.setattr(LLMNodeExecutor, "execute", execute)
    graph = definition()
    with pytest.raises(WorkflowPaused) as paused:
        await WorkflowExecutor(graph, project_id="project", execution_id="run").run(
            {}, on_checkpoint=pause_after_fork,
        )
    assert set(paused.value.checkpoint.completed_nodes) == {
        "start", "fork", "first", "second",
    }
    result = await WorkflowExecutor(graph, project_id="project", execution_id="run").run(
        {}, checkpoint=paused.value.checkpoint,
    )
    assert result == "first + second"
    assert calls == ["first", "second"]
