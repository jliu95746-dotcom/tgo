"""Malformed definitions must stop before records, nodes, or broker work."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import executions
from app.database import get_db
from app.models.execution import WorkflowExecution
from app.models.execution import NodeExecution
from app.models.approval import WorkflowApprovalRequest
from app.services import execution_snapshot
from app.services.validation_service import ValidationService
from tests.unit.test_execution_snapshots import answer_definition


MARKER = "synthetic-private-execution-marker"
BAD_DEFINITIONS = [
    None,
    [],
    MARKER,
    7,
    {"nodes": None},
    {"nodes": {}},
    {"nodes": [None]},
    {"nodes": [{"id": [], "type": "input", "data": {}}]},
    {"nodes": [{"id": "input", "type": [], "data": {}}]},
    {"nodes": [{"id": "input", "type": "input", "data": None}]},
    {
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"label": {"private": MARKER}},
            }
        ]
    },
    {"nodes": [], "edges": None},
    {"nodes": [], "edges": {}},
    {"nodes": [], "edges": [None]},
    {"nodes": [], "edges": [{"source": [], "target": "input"}]},
    {
        "nodes": [],
        "edges": [{"source": "input", "target": {"private": MARKER}}],
    },
    {
        "nodes": [],
        "edges": [{"source": "input", "target": "answer", "sourceHandle": []}],
    },
]


@pytest.mark.parametrize("definition", BAD_DEFINITIONS)
def test_validator_returns_errors_for_invalid_saved_shapes(definition):
    errors = ValidationService.validate_workflow(definition)
    assert errors and all(isinstance(error, str) for error in errors)
    assert MARKER not in str(errors)


@pytest.mark.asyncio
@pytest.mark.parametrize("definition", BAD_DEFINITIONS)
@pytest.mark.parametrize("mode", ["sync", "stream", "async"])
async def test_invalid_definition_does_not_create_or_dispatch(
    monkeypatch, definition, mode
):
    workflow = SimpleNamespace(
        id="workflow",
        project_id="project",
        status="draft",
        version=1,
        definition=definition,
    )
    monkeypatch.setattr(
        executions.WorkflowService,
        "get_by_id",
        AsyncMock(return_value=workflow),
    )
    create = AsyncMock(
        side_effect=AssertionError("must not accept invalid definition")
    )
    enqueue = AsyncMock(
        side_effect=AssertionError("must not send invalid broker work")
    )
    runner = Mock(side_effect=AssertionError("must not instantiate executor"))
    monkeypatch.setattr(executions, "_create_execution_record", create)
    monkeypatch.setattr(executions, "enqueue_workflow", enqueue)
    monkeypatch.setattr(executions, "WorkflowExecutor", runner)
    app = FastAPI()
    app.include_router(executions.router, prefix="/v1/workflows")
    app.dependency_overrides[get_db] = lambda: object()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/workflows/workflow/execute",
            params={"project_id": "project"},
            json={
                "inputs": {},
                "stream": mode == "stream",
                "async": mode == "async",
            },
        )
    assert response.status_code == 400
    assert response.json()["detail"]["errors"]
    assert MARKER not in response.text
    create.assert_not_awaited()
    enqueue.assert_not_awaited()
    runner.assert_not_called()


def captured_run():
    definition = answer_definition()
    return WorkflowExecution(
        id="run",
        workflow_id="workflow",
        project_id="project",
        status="running",
        workflow_version=1,
        input={"question": "original"},
        definition_snapshot=definition,
        definition_digest=execution_snapshot.definition_digest(definition),
    )


@pytest.mark.parametrize(
    "changed", ["input", "definition", "digest", "tampered", "input-shape"]
)
def test_missing_or_changed_capture_never_falls_back_to_live_arguments(
    changed,
):
    row = captured_run()
    if changed == "input":
        row.input = None
    elif changed == "definition":
        row.definition_snapshot = None
    elif changed == "digest":
        row.definition_digest = None
    elif changed == "input-shape":
        row.input = [MARKER]
    else:
        row.definition_snapshot = answer_definition(MARKER)
    with pytest.raises(RuntimeError) as error:
        execution_snapshot.captured_execution_payload(row)
    assert MARKER not in str(error.value)


def test_captured_payload_is_owned_and_preserves_original_data():
    row = captured_run()
    original = deepcopy(row.definition_snapshot)
    inputs, definition = execution_snapshot.captured_execution_payload(row)
    inputs["question"] = "changed"
    definition["nodes"] = []
    assert row.input == {"question": "original"}
    assert row.definition_snapshot == original


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sync", "stream"])
@pytest.mark.parametrize("changed", ["input", "definition", "tampered"])
@pytest.mark.parametrize("prior_status", ["running", "cancelled"])
async def test_corrupted_capture_is_stopped_before_direct_execution(
    monkeypatch, mode, changed, prior_status
):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)")
            )
            await connection.run_sync(WorkflowExecution.__table__.create)
            await connection.run_sync(WorkflowApprovalRequest.__table__.create)
            await connection.run_sync(NodeExecution.__table__.create)
        async with sessions() as db:
            await db.execute(
                text("INSERT INTO wf_workflows (id) VALUES ('workflow')")
            )
            await db.commit()
        workflow = SimpleNamespace(
            id="workflow",
            project_id="project",
            status="draft",
            version=1,
            definition=answer_definition(),
        )
        monkeypatch.setattr(
            executions.WorkflowService,
            "get_by_id",
            AsyncMock(return_value=workflow),
        )
        original = executions._create_execution_record

        async def corrupt(*args, **kwargs):
            run = await original(*args, **kwargs)
            if changed == "input":
                run.input = None
            elif changed == "definition":
                run.definition_snapshot = None
            else:
                run.definition_snapshot = answer_definition(MARKER)
            run.status = prior_status
            await args[0].commit()
            return run

        monkeypatch.setattr(executions, "_create_execution_record", corrupt)
        runner = Mock(
            side_effect=AssertionError("corrupted capture must not execute")
        )
        monkeypatch.setattr(executions, "WorkflowExecutor", runner)
        app = FastAPI()
        app.include_router(executions.router, prefix="/v1/workflows")

        async def database():
            async with sessions() as db:
                yield db

        app.dependency_overrides[get_db] = database
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/workflows/workflow/execute",
                params={"project_id": "project"},
                json={
                    "inputs": {"question": "original"},
                    "stream": mode == "stream",
                },
            )
        assert response.status_code == 409 and MARKER not in response.text
        runner.assert_not_called()
        async with sessions() as db:
            run = (await db.scalars(select(WorkflowExecution))).one()
            assert run.status == (
                "failed" if prior_status == "running" else "cancelled"
            )
            assert not (await db.scalars(select(NodeExecution))).all()
    finally:
        await engine.dispose()
