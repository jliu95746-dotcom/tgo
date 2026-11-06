"""Workflow deletion keeps tenant boundaries and caller transactions."""

from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import workflows
from app.database import Base, get_db
from app.models.execution import NodeExecution, WorkflowExecution
from app.models.workflow import Workflow
from app.services.workflow_service import WorkflowService


@pytest.fixture
async def sessions(tmp_path):
    engine = create_async_engine(
        "sqlite+aiosqlite:///" + (tmp_path / "deletion.sqlite").as_posix()
    )

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def seed(sessions, project="project"):
    flow_id, run_id, node_id = (str(uuid4()) for _ in range(3))
    async with sessions() as db:
        db.add(
            Workflow(
                id=flow_id,
                project_id=project,
                name="synthetic deletion",
                definition={"nodes": [], "edges": []},
                tags=[],
            )
        )
        await db.flush()
        db.add(
            WorkflowExecution(
                id=run_id,
                workflow_id=flow_id,
                project_id=project,
                status="completed",
                input=None,
                output=None,
            )
        )
        await db.flush()
        db.add(
            NodeExecution(
                id=node_id,
                execution_id=run_id,
                project_id=project,
                node_id="answer",
                node_type="answer",
                status="completed",
            )
        )
        await db.commit()
    return flow_id, run_id, node_id


@pytest.mark.asyncio
@pytest.mark.parametrize("commit", [True, False])
async def test_delete_result_and_caller_transaction(sessions, commit):
    flow_id, run_id, node_id = await seed(sessions)
    other = await seed(sessions, "another-project")
    async with sessions() as db:
        assert await WorkflowService.delete(db, flow_id, "project") is True
        await (db.commit() if commit else db.rollback())
    async with sessions() as db:
        for model, identifier in zip(
            (Workflow, WorkflowExecution, NodeExecution),
            (flow_id, run_id, node_id),
        ):
            assert (await db.get(model, identifier) is None) == commit
        for model, identifier in zip(
            (Workflow, WorkflowExecution, NodeExecution), other
        ):
            assert await db.get(model, identifier) is not None


@pytest.mark.asyncio
async def test_wrong_project_and_missing_workflow_preserve_records(sessions):
    identifiers = await seed(sessions)
    async with sessions() as db:
        assert not await WorkflowService.delete(
            db, identifiers[0], "another-project"
        )
        assert not await WorkflowService.delete(db, str(uuid4()), "project")
        await db.commit()
    async with sessions() as db:
        for model, identifier in zip(
            (Workflow, WorkflowExecution, NodeExecution), identifiers
        ):
            assert await db.get(model, identifier) is not None


@pytest.mark.asyncio
async def test_delete_http_status_and_repeat(sessions):
    flow_id, _, _ = await seed(sessions)
    app = FastAPI()
    app.include_router(workflows.router, prefix="/v1/workflows")

    async def database():
        async with sessions() as db:
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    app.dependency_overrides[get_db] = database
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        path = "/v1/workflows/" + flow_id
        wrong = await client.delete(
            path, params={"project_id": "another-project"}
        )
        assert wrong.status_code == 404
        first = await client.delete(path, params={"project_id": "project"})
        assert first.status_code == 204 and first.content == b""
        repeated = await client.delete(path, params={"project_id": "project"})
        assert repeated.status_code == 404
