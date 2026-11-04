"""A persisted approval checkpoint must not replay completed workflow nodes."""

import pytest

from app.engine.checkpoint import ExecutionCheckpoint, WorkflowPaused
from app.engine.executor import WorkflowExecutor


def definition():
    return {
        "nodes": [
            {"id": "start", "type": "input", "data": {
                "reference_key": "input", "label": "客户输入",
                "input_variables": [{"name": "order", "type": "string"}],
            }},
            {"id": "check", "type": "condition", "data": {
                "reference_key": "check", "label": "检查订单",
                "condition_type": "variable", "variable": "input.order",
                "operator": "equals", "compare_value": "OWNED",
            }},
            {"id": "approved", "type": "answer", "data": {
                "reference_key": "approved", "label": "确认通过",
                "output_type": "template", "output_template": "已核对 {{input.order}}",
            }},
            {"id": "rejected", "type": "answer", "data": {
                "reference_key": "rejected", "label": "未通过",
                "output_type": "template", "output_template": "未通过",
            }},
        ],
        "edges": [
            {"source": "start", "target": "check"},
            {"source": "check", "target": "approved", "sourceHandle": "true"},
            {"source": "check", "target": "rejected", "sourceHandle": "false"},
        ],
    }


@pytest.mark.asyncio
async def test_resuming_checkpoint_runs_only_unfinished_branch():
    completed = []

    async def node_complete(**receipt):
        completed.append(receipt["node_id"])

    async def pause_after_check(node_id, node_type, checkpoint):
        return node_id == "check"

    executor = WorkflowExecutor(
        definition(), project_id="project-1", execution_id="run-1",
    )
    with pytest.raises(WorkflowPaused) as stopped:
        await executor.run(
            {"order": "OWNED"}, on_node_complete=node_complete,
            on_checkpoint=pause_after_check,
        )
    assert completed == ["start", "check"]
    saved = ExecutionCheckpoint.model_validate_json(
        stopped.value.checkpoint.model_dump_json()
    )
    assert saved.completed_nodes == ["start", "check"]
    assert saved.current_nodes == []
    assert saved.next_nodes == ["approved"]

    completed.clear()
    resumed = WorkflowExecutor(
        definition(), project_id="project-1", execution_id="run-1",
    )
    assert await resumed.run(
        {"order": "OWNED"}, checkpoint=saved, on_node_complete=node_complete,
    ) == "已核对 OWNED"
    assert completed == ["approved"]


@pytest.mark.asyncio
async def test_checkpoint_rejects_other_tenant_run_or_definition():
    async def pause_after_start(node_id, node_type, checkpoint):
        return node_id == "start"

    with pytest.raises(WorkflowPaused) as stopped:
        await WorkflowExecutor(
            definition(), project_id="project-1", execution_id="run-1",
        ).run({"order": "OWNED"}, on_checkpoint=pause_after_start)
    checkpoint = stopped.value.checkpoint

    with pytest.raises(ValueError, match="检查点"):
        await WorkflowExecutor(
            definition(), project_id="project-1", execution_id="run-1",
        ).run({"order": "OTHER"}, checkpoint=checkpoint)

    for project, run in (("project-2", "run-1"), ("project-1", "run-2")):
        with pytest.raises(ValueError, match="检查点"):
            await WorkflowExecutor(
                definition(), project_id=project, execution_id=run,
            ).run({"order": "OWNED"}, checkpoint=checkpoint)

    edited = definition()
    edited["nodes"][2]["data"]["output_template"] = "配置已更改"
    with pytest.raises(ValueError, match="检查点"):
        await WorkflowExecutor(
            edited, project_id="project-1", execution_id="run-1",
        ).run({"order": "OWNED"}, checkpoint=checkpoint)


@pytest.mark.asyncio
async def test_checkpoint_rejects_forged_or_duplicate_node_positions():
    async def pause_after_start(node_id, node_type, checkpoint):
        return node_id == "start"

    with pytest.raises(WorkflowPaused) as stopped:
        await WorkflowExecutor(
            definition(), project_id="project-1", execution_id="run-1",
        ).run({"order": "OWNED"}, on_checkpoint=pause_after_start)
    forged = stopped.value.checkpoint.model_copy(update={
        "next_nodes": ["missing"],
    })
    with pytest.raises(ValueError, match="检查点"):
        await WorkflowExecutor(
            definition(), project_id="project-1", execution_id="run-1",
        ).run({"order": "OWNED"}, checkpoint=forged)

    duplicate = stopped.value.checkpoint.model_copy(update={
        "completed_nodes": ["start", "start"],
    })
    with pytest.raises(ValueError, match="检查点"):
        await WorkflowExecutor(
            definition(), project_id="project-1", execution_id="run-1",
        ).run({"order": "OWNED"}, checkpoint=duplicate)


@pytest.mark.asyncio
async def test_checkpoint_keeps_unfinished_nodes_in_the_current_wave():
    graph = {
        "nodes": [
            {"id": "start", "type": "input", "data": {
                "reference_key": "input", "label": "输入", "input_variables": [],
            }},
            {"id": "first", "type": "answer", "data": {
                "reference_key": "first", "label": "第一支",
                "output_type": "template", "output_template": "第一支",
            }},
            {"id": "second", "type": "answer", "data": {
                "reference_key": "second", "label": "第二支",
                "output_type": "template", "output_template": "第二支",
            }},
        ],
        "edges": [
            {"source": "start", "target": "first"},
            {"source": "start", "target": "second"},
        ],
    }
    completed = []

    async def on_node_complete(**receipt):
        completed.append(receipt["node_id"])

    async def pause_first(node_id, node_type, checkpoint):
        return node_id == "first"

    with pytest.raises(WorkflowPaused) as stopped:
        await WorkflowExecutor(graph, project_id="p", execution_id="r").run(
            {}, on_node_complete=on_node_complete, on_checkpoint=pause_first,
        )
    assert stopped.value.checkpoint.current_nodes == ["second"]
    assert completed == ["start", "first"]
    completed.clear()
    assert await WorkflowExecutor(graph, project_id="p", execution_id="r").run(
        {}, checkpoint=stopped.value.checkpoint,
        on_node_complete=on_node_complete,
    ) == "第二支"
    assert completed == ["second"]
