"""Child runs are listed only through an owned, authenticated parent."""

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
async def test_parent_lookup_is_project_scoped_authenticated_and_paged(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr("trace-test-secret"))
    project_id, foreign_project_id = str(uuid4()), str(uuid4())
    parent_id, foreign_parent_id = str(uuid4()), str(uuid4())
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
            for run_id, owner, parent in (
                (parent_id, project_id, None),
                (foreign_parent_id, foreign_project_id, None),
                ("child-c", project_id, parent_id),
                ("child-b", project_id, parent_id),
                ("child-a", project_id, parent_id),
                ("foreign-child", foreign_project_id, parent_id),
                ("other-child", project_id, foreign_parent_id),
            ):
                db.add(WorkflowExecution(
                    id=run_id, project_id=owner, workflow_id="workflow-a",
                    parent_execution_id=parent, status="completed",
                    input={"private": "do-not-return"}, started_at=started_at,
                ))
            await db.commit()
        app = FastAPI()
        app.include_router(executions.router, prefix="/v1/workflows")

        async def test_session():
            async with sessions() as db:
                yield db

        app.dependency_overrides[get_db] = test_session
        path = f"/v1/workflows/executions/by-parent/{parent_id}"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            denied = await client.get(path, params={"project_id": project_id})
            assert denied.status_code == 403
            wrong_project = await client.get(
                path, params={"project_id": foreign_project_id},
                headers={"X-Internal-API-Key": "trace-test-secret"},
            )
            assert wrong_project.status_code == 404
            pages = []
            for skip in (0, 2):
                response = await client.get(
                    path,
                    params={"project_id": project_id, "limit": 2, "skip": skip},
                    headers={"X-Internal-API-Key": "trace-test-secret"},
                )
                assert response.status_code == 200
                pages.append(response.json())
        assert [[item["id"] for item in page] for page in pages] == [
            ["child-c", "child-b"], ["child-a"],
        ]
        assert all(item["parent_execution_id"] == parent_id for page in pages for item in page)
        assert "do-not-return" not in str(pages)
    finally:
        await engine.dispose()
