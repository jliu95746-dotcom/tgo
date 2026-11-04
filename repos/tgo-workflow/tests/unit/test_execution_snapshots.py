"""An accepted run executes its persisted definition and inputs, never a later draft."""

from copy import deepcopy
import json
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import executions
from app.models.execution import WorkflowExecution, NodeExecution
from app.models.approval import WorkflowApprovalRequest
from app.models.workflow import Workflow  # noqa: F401 - register the foreign-key parent
from app.services.workflow_service import WorkflowService
from app.services.employee_workflow_binding import saved_employee_binding
from celery_app import tasks


def answer_definition(value="accepted-version"):
    return {
        "nodes": [
            {"id": "input", "type": "input", "data": {
                "reference_key": "input",
                "input_variables": [{"name": "question", "type": "string"}],
            }},
            {"id": "answer", "type": "answer", "data": {
                "reference_key": "answer", "output_type": "template",
                "output_template": value + " {{input.question}}",
            }},
        ],
        "edges": [{"source": "input", "target": "answer"}],
    }


@pytest.mark.asyncio
async def test_execution_record_owns_a_deep_copy_and_version():
    db = AsyncMock()
    db.add = Mock()
    definition = answer_definition()
    original = deepcopy(definition)
    inputs = {"question": "accepted-input"}
    row = await executions._create_execution_record(
        db, "owned-workflow", "owned-project", inputs,
        workflow_definition=definition, workflow_version=7,
    )
    definition["nodes"][1]["data"]["output_template"] = "later-draft"
    inputs["question"] = "later-input"
    assert row.definition_snapshot == original
    assert row.workflow_version == 7
    assert row.input == {"question": "accepted-input"}
    assert len(row.definition_digest) == 64


@pytest.mark.asyncio
@pytest.mark.parametrize("corrupt", [False, True])
async def test_worker_uses_persisted_snapshot_and_inputs(monkeypatch, corrupt):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            # Only a foreign-key parent is needed; ARRAY tags remain PostgreSQL-specific.
            await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
            await connection.run_sync(WorkflowExecution.__table__.create)
            await connection.run_sync(WorkflowApprovalRequest.__table__.create)
            await connection.run_sync(NodeExecution.__table__.create)
        async with sessions() as db:
            await db.execute(text("INSERT INTO wf_workflows (id) VALUES ('owned-workflow')"))
            row = await executions._create_execution_record(
                db, "owned-workflow", "owned-project", {"question": "accepted-input"},
                status="pending", workflow_definition=answer_definition(), workflow_version=7,
            )
            run_id = row.id
            if corrupt:
                row.definition_snapshot = answer_definition("tampered-version")
                await db.commit()
        monkeypatch.setattr(tasks, "workflow_task_session", sessions)
        current_draft = AsyncMock(side_effect=AssertionError("must not read mutable draft"))
        monkeypatch.setattr(WorkflowService, "get_by_id", current_draft)
        await tasks.async_execute_workflow(
            run_id, "owned-workflow", {"question": "untrusted-broker-input"}, "owned-project",
        )
        async with sessions() as db:
            saved = (await db.execute(select(WorkflowExecution).where(
                WorkflowExecution.id == run_id,
            ))).scalar_one()
            assert saved.status == ("failed" if corrupt else "completed")
            if not corrupt:
                assert saved.output == {"result": "accepted-version accepted-input"}
            else:
                assert "tampered-version" not in (saved.error or "")
        current_draft.assert_not_awaited()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_queued_employee_run_stops_before_next_node_after_revocation(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
            await connection.run_sync(WorkflowExecution.__table__.create)
            await connection.run_sync(WorkflowApprovalRequest.__table__.create)
            await connection.run_sync(NodeExecution.__table__.create)
        async with sessions() as db:
            await db.execute(text("INSERT INTO wf_workflows (id) VALUES ('owned-workflow')"))
            row = await executions._create_execution_record(
                db, "owned-workflow", "owned-project", {"question": "accepted"},
                status="pending", workflow_definition=answer_definition(),
                workflow_version=2, execution_mode="production",
                release_id=str(uuid4()), employee_agent_id=str(uuid4()),
            )
            run_id = row.id
        check = AsyncMock(side_effect=[None, None, PermissionError("revoked")])
        monkeypatch.setattr(tasks, "workflow_task_session", sessions)
        monkeypatch.setattr(tasks, "check_employee_binding", check, raising=False)

        await tasks.async_execute_workflow(
            run_id, "owned-workflow", {"question": "untrusted"}, "owned-project"
        )

        async with sessions() as db:
            saved = await db.get(WorkflowExecution, run_id)
            nodes = (await db.scalars(select(NodeExecution).where(
                NodeExecution.execution_id == run_id,
            ))).all()
            assert saved.status == "failed"
            assert saved.output is None
            assert [node.node_type for node in nodes] == ["input"]
        assert check.await_count == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_employee_run_does_not_publish_answer_after_final_binding_revocation(
    monkeypatch,
):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
            await connection.run_sync(WorkflowExecution.__table__.create)
            await connection.run_sync(WorkflowApprovalRequest.__table__.create)
            await connection.run_sync(NodeExecution.__table__.create)
        async with sessions() as db:
            await db.execute(text("INSERT INTO wf_workflows (id) VALUES ('owned-workflow')"))
            row = await executions._create_execution_record(
                db, "owned-workflow", "owned-project", {"question": "accepted"},
                status="pending", workflow_definition=answer_definition(),
                workflow_version=2, execution_mode="production",
                release_id=str(uuid4()), employee_agent_id=str(uuid4()),
            )
            run_id = row.id
        check = AsyncMock(side_effect=[None] * 4 + [PermissionError("revoked")])
        monkeypatch.setattr(tasks, "workflow_task_session", sessions)
        monkeypatch.setattr(tasks, "check_employee_binding", check)

        await tasks.async_execute_workflow(run_id, "owned-workflow", {}, "owned-project")

        async with sessions() as db:
            saved = await db.get(WorkflowExecution, run_id)
            assert saved.status == "failed"
            assert saved.output is None
        assert check.await_count == 5
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_live_employee_run_stops_before_next_node_after_revocation(
    monkeypatch, stream,
):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
            await connection.run_sync(WorkflowExecution.__table__.create)
            await connection.run_sync(WorkflowApprovalRequest.__table__.create)
            await connection.run_sync(NodeExecution.__table__.create)
        async with sessions() as db:
            await db.execute(text("INSERT INTO wf_workflows (id) VALUES ('owned-workflow')"))
            row = await executions._create_execution_record(
                db, "owned-workflow", "owned-project", {"question": "accepted"},
                workflow_definition=answer_definition(), workflow_version=2,
                execution_mode="production", release_id=str(uuid4()),
                employee_agent_id=str(uuid4()),
            )
            run_id = row.id
            binding = saved_employee_binding(
                row.project_id, row.employee_agent_id, row.workflow_id,
                row.execution_mode, row.workflow_version,
                row.release_id, row.definition_digest,
            )
            check = AsyncMock(side_effect=[None, None, PermissionError("revoked")])
            monkeypatch.setattr(executions, "check_employee_binding", check)
            arguments = (
                row.workflow_id, row.project_id, run_id, row.input,
                row.definition_snapshot, row.started_at, db,
            )
            if stream:
                events = [json.loads(frame[6:]) async for frame in
                          executions._run_stream_execution(
                              *arguments, employee_binding=binding,
                          )]
                assert events[-1]["data"]["status"] == "failed"
                assert events[-1]["data"]["outputs"] == {}
            else:
                result = await executions._run_sync_execution(
                    *arguments, employee_binding=binding,
                )
                assert result["success"] is False
                assert result["output"] == {}
            db.expire_all()
            saved = await db.get(WorkflowExecution, run_id)
            nodes = (await db.scalars(select(NodeExecution).where(
                NodeExecution.execution_id == run_id,
            ))).all()
            assert saved.status == "failed"
            assert saved.output is None
            assert [node.node_type for node in nodes] == ["input"]
            assert check.await_count == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_old_execution_without_snapshot_is_not_rebuilt_from_current_draft(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
            await connection.run_sync(WorkflowExecution.__table__.create)
            await connection.run_sync(WorkflowApprovalRequest.__table__.create)
            await connection.run_sync(NodeExecution.__table__.create)
        async with sessions() as db:
            await db.execute(text("INSERT INTO wf_workflows (id) VALUES ('owned-workflow')"))
            row = WorkflowExecution(
                id="legacy-run", project_id="owned-project", workflow_id="owned-workflow",
                status="pending", input={},
            )
            db.add(row)
            await db.commit()
        monkeypatch.setattr(tasks, "workflow_task_session", sessions)
        invoke = Mock()
        monkeypatch.setattr(tasks, "WorkflowExecutor", invoke)
        monkeypatch.setattr(WorkflowService, "get_by_id", AsyncMock(
            side_effect=AssertionError("must not invent a snapshot"),
        ))
        await tasks.async_execute_workflow("legacy-run", "owned-workflow", {}, "owned-project")
        async with sessions() as db:
            saved = await db.get(WorkflowExecution, "legacy-run")
            assert saved.status == "failed"
        invoke.assert_not_called()
    finally:
        await engine.dispose()
