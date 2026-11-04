"""A failed draft run stays failed in the API result, events, and saved record."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import executions
from app.models.execution import NodeExecution, WorkflowExecution
from app.models.approval import WorkflowApprovalRequest
from app.models.workflow import Workflow  # noqa: F401 - register the foreign-key parent


def failing_definition():
    return {
        "nodes": [
            {"id": "input", "type": "input", "data": {
                "label": "输入", "reference_key": "input",
                "input_variables": [{"name": "amount", "type": "number"}],
            }},
            {"id": "check", "type": "condition", "data": {
                "label": "金额判断", "reference_key": "check",
                "condition_type": "variable", "variable": "input.amount",
                "operator": "greaterThan", "compare_value": "private-value",
            }},
            {"id": "answer", "type": "answer", "data": {
                "label": "回答", "reference_key": "answer",
                "output_type": "template", "output_template": "不应输出",
            }},
        ],
        "edges": [
            {"source": "input", "target": "check"},
            {"source": "check", "target": "answer", "sourceHandle": "true"},
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_failed_draft_run_is_visible_and_never_returns_an_answer(stream):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    definition = failing_definition()
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
            await connection.run_sync(WorkflowExecution.__table__.create)
            await connection.run_sync(WorkflowApprovalRequest.__table__.create)
            await connection.run_sync(NodeExecution.__table__.create)
        async with sessions() as db:
            await db.execute(text("INSERT INTO wf_workflows (id) VALUES ('flow-a')"))
            row = await executions._create_execution_record(
                db, "flow-a", "project-a", {"amount": 10},
                status="running", workflow_definition=definition,
                execution_mode="draft_test",
            )
            run_id = row.id
            arguments = (
                "flow-a", "project-a", run_id, {"amount": 10}, definition,
                datetime.now(timezone.utc), db,
            )
            if stream:
                events = [json.loads(frame[6:]) async for frame in
                          executions._run_stream_execution(*arguments)]
                assert events[-1]["event"] == "workflow_finished"
                assert events[-1]["data"]["status"] == "failed"
                assert events[-1]["data"]["outputs"] == {}
                assert "金额判断" in events[-1]["data"]["error"]
                assert "private-value" not in events[-1]["data"]["error"]
                assert any(event["event"] == "node_finished" and
                           event["data"]["status"] == "failed" for event in events)
                assert not any(event.get("data", {}).get("node_id") == "answer"
                               for event in events)
            else:
                result = await executions._run_sync_execution(*arguments)
                assert result["success"] is False
                assert result["output"] == {}
            db.expire_all()
            saved = await db.get(WorkflowExecution, run_id)
            nodes = (await db.execute(select(NodeExecution).where(
                NodeExecution.execution_id == run_id,
            ))).scalars().all()
            assert saved.status == "failed"
            assert saved.execution_mode == "draft_test"
            assert saved.output is None
            assert "金额判断" in saved.error
            assert "private-value" not in saved.error
            assert [(node.node_id, node.status) for node in nodes] == [
                ("input", "completed"), ("check", "failed"),
            ]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_unexpected_failure_does_not_publish_or_save_exception_body(
    stream, monkeypatch, caplog,
):
    marker = "private-customer-answer-9071"
    monkeypatch.setattr(
        executions.WorkflowExecutor, "run",
        AsyncMock(side_effect=RuntimeError(marker)),
    )
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
            await connection.run_sync(WorkflowExecution.__table__.create)
            await connection.run_sync(WorkflowApprovalRequest.__table__.create)
            await connection.run_sync(NodeExecution.__table__.create)
        async with sessions() as db:
            await db.execute(text("INSERT INTO wf_workflows (id) VALUES ('flow-a')"))
            definition = failing_definition()
            row = await executions._create_execution_record(
                db, "flow-a", "project-a", {"amount": 10},
                status="running", workflow_definition=definition,
                execution_mode="draft_test",
            )
            run_id = row.id
            arguments = (
                "flow-a", "project-a", run_id, {"amount": 10}, definition,
                datetime.now(timezone.utc), db,
            )
            if stream:
                events = [frame async for frame in
                          executions._run_stream_execution(*arguments)]
                assert marker not in "".join(events)
            else:
                result = await executions._run_sync_execution(*arguments)
                assert marker not in str(result)
            db.expire_all()
            saved = await db.get(WorkflowExecution, run_id)
            assert saved.status == "failed"
            assert marker not in (saved.error or "") + caplog.text
            assert "RuntimeError" in (saved.error or "")
    finally:
        await engine.dispose()
