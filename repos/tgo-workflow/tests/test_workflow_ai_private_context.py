"""Private quota metadata never appears in ordinary execution history."""
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import ai_runtime, executions
from app.config import settings
from app.database import get_db
from app.models.execution import NodeExecution, WorkflowExecution
from app.schemas.ai_usage import WorkflowUsageOwner
from app.services.execution_snapshot import definition_digest


@pytest.mark.asyncio
async def test_private_context_authentication_and_history_redaction(
    monkeypatch,
):
    monkeypatch.setattr(
        settings, "SECRET_KEY", SecretStr("synthetic-private-context")
    )
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    project, execution, agent, actor = uuid4(), uuid4(), uuid4(), uuid4()
    definition = {
        "nodes": [
            {"id": "agent", "type": "agent", "data": {"agent_id": str(agent)}}
        ],
        "edges": [],
    }
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)")
            )
            await connection.run_sync(WorkflowExecution.__table__.create)
            await connection.run_sync(NodeExecution.__table__.create)
        app = FastAPI()
        app.include_router(ai_runtime.router, prefix="/v1/workflows")
        app.include_router(executions.router, prefix="/v1/workflows")

        async def database():
            async with factory() as db:
                yield db

        app.dependency_overrides[get_db] = database
        async with factory() as db:
            row = await executions._create_execution_record(
                db,
                "owned",
                str(project),
                {},
                workflow_definition=definition,
                workflow_version=1,
                execution_id=str(execution),
                usage_owner=WorkflowUsageOwner(
                    staff_id=actor, staff_token_version=3
                ),
            )
            assert row.ai_usage_owner["staff_id"] == str(actor)
            assert row.definition_digest == definition_digest(definition)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            url = f"/v1/workflows/executions/{execution}/ai-context"
            params = {"project_id": str(project), "node_id": "agent"}
            assert (await client.get(url, params=params)).status_code == 403
            assert (
                await client.get(
                    url, params=params, headers={"X-Internal-API-Key": "wrong"}
                )
            ).status_code == 403
            response = await client.get(
                url,
                params=params,
                headers={"X-Internal-API-Key": "synthetic-private-context"},
            )
            assert response.status_code == 200
            assert response.json()["owner"]["staff_token_version"] == 3
            assert (
                await client.get(
                    url,
                    params={**params, "project_id": str(uuid4())},
                    headers={
                        "X-Internal-API-Key": "synthetic-private-context"
                    },
                )
            ).status_code == 403
            public = await client.get(
                f"/v1/workflows/executions/{execution}",
                params={"project_id": str(project)},
            )
            assert public.status_code == 200
            assert (
                "ai_usage_owner" not in public.text
                and str(actor) not in public.text
            )
    finally:
        await engine.dispose()
