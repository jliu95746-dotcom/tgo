"""Tenant-scoped execution history has deterministic offset pages."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import executions
from app.config import settings
from app.database import get_db
from app.models.execution import NodeExecution, WorkflowExecution


@pytest.mark.asyncio
async def test_business_request_history_pages_keep_order_and_tenant_scope(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr("trace-test-secret"))
    project_id, other_project_id, request_id = uuid4(), uuid4(), uuid4()
    started_at = datetime(2026, 10, 14, tzinfo=UTC)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
            await connection.run_sync(WorkflowExecution.__table__.create)
            await connection.run_sync(NodeExecution.__table__.create)
        async with sessions() as db:
            await db.execute(text("INSERT INTO wf_workflows (id) VALUES ('workflow-a')"))
            for run_id, owner in (("run-c", project_id), ("run-b", project_id),
                                  ("run-a", project_id), ("run-z", other_project_id)):
                db.add(WorkflowExecution(
                    id=run_id, project_id=str(owner), workflow_id="workflow-a",
                    status="completed", input={}, business_request_id=str(request_id),
                    started_at=started_at,
                ))
            await db.commit()
        app = FastAPI()
        app.include_router(executions.router, prefix="/v1/workflows")

        async def test_session():
            async with sessions() as db:
                yield db

        app.dependency_overrides[get_db] = test_session
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            pages = []
            for skip in (0, 2):
                result = await client.get(
                    f"/v1/workflows/executions/by-business-request/{request_id}",
                    params={"project_id": str(project_id), "limit": 2, "skip": skip},
                    headers={"X-Internal-API-Key": "trace-test-secret"},
                )
                assert result.status_code == 200
                pages.append([row["id"] for row in result.json()])
        assert pages == [["run-c", "run-b"], ["run-a"]]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_history_anchor_survives_newer_run_and_rejects_half_anchor(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr("trace-test-secret"))
    project_id, foreign_project_id, request_id = uuid4(), uuid4(), uuid4()
    started_at = datetime(2026, 10, 14, tzinfo=UTC)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
            await connection.run_sync(WorkflowExecution.__table__.create)
            await connection.run_sync(NodeExecution.__table__.create)
        async with sessions() as db:
            await db.execute(text("INSERT INTO wf_workflows (id) VALUES ('workflow-a')"))
            for run_id, owner in (("run-c", project_id), ("run-b", project_id),
                                  ("run-a", project_id), ("run-z", foreign_project_id)):
                db.add(WorkflowExecution(
                    id=run_id, project_id=str(owner), workflow_id="workflow-a",
                    status="completed", input={}, business_request_id=str(request_id),
                    started_at=started_at,
                ))
            await db.commit()
        app = FastAPI()
        app.include_router(executions.router, prefix="/v1/workflows")

        async def test_session():
            async with sessions() as db:
                yield db

        app.dependency_overrides[get_db] = test_session
        headers = {"X-Internal-API-Key": "trace-test-secret"}
        path = f"/v1/workflows/executions/by-business-request/{request_id}"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.get(path, params={"project_id": str(project_id), "limit": 2}, headers=headers)
            assert first.status_code == 200
            assert [row["id"] for row in first.json()] == ["run-c", "run-b"]
            anchor = first.json()[-1]
            async with sessions() as db:
                db.add(WorkflowExecution(
                    id="run-new", project_id=str(project_id), workflow_id="workflow-a",
                    status="completed", input={}, business_request_id=str(request_id),
                    started_at=datetime(2026, 10, 15, tzinfo=UTC),
                ))
                await db.commit()
            params = {
                "project_id": str(project_id), "limit": 2,
                "before_started_at": anchor["started_at"], "before_id": anchor["id"],
            }
            second = await client.get(path, params=params, headers=headers)
            workflow_second = await client.get(
                "/v1/workflows/workflow-a/executions", params=params,
            )
            half = await client.get(path, params={
                "project_id": str(project_id), "before_id": anchor["id"],
            }, headers=headers)
            foreign = await client.get(path, params={
                **params, "project_id": str(foreign_project_id),
            }, headers=headers)
        assert second.status_code == 200, second.text
        assert [row["id"] for row in second.json()] == ["run-a"]
        assert [row["id"] for row in workflow_second.json()] == ["run-a"]
        assert half.status_code == 400
        assert foreign.json() == []
    finally:
        await engine.dispose()
