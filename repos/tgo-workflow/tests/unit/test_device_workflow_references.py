"""A device card reports pinned draft and live workflow references separately."""

from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.workflows import router
from app.database import get_db
from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease


def definition(device_id: str | None) -> dict:
    data = {"agent_id": str(uuid4())}
    if device_id:
        data.update(expected_device_id=device_id, expected_device_policy_digest="a" * 64)
    return {"nodes": [{"id": "agent", "type": "agent", "data": data}], "edges": []}


@pytest.mark.asyncio
async def test_device_references_use_current_release_and_separate_draft_with_tenant_scope():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Workflow.__table__.create)
        await connection.run_sync(WorkflowRelease.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    device_a, device_b = str(uuid4()), str(uuid4())
    active_id, draft_id, both_id = str(uuid4()), str(uuid4()), str(uuid4())
    async with sessions() as db:
        db.add_all([
            Workflow(id=active_id, project_id="owned", name="订单查询", status="active",
                     definition=definition(device_b), published_version=1, tags=[]),
            Workflow(id=draft_id, project_id="owned", name="目录整理", status="draft",
                     definition=definition(device_a), tags=[]),
            Workflow(id=both_id, project_id="owned", name="共享流程", status="active",
                     definition=definition(device_a), published_version=1, tags=[]),
            Workflow(id=str(uuid4()), project_id="foreign", name="外部流程", status="draft",
                     definition=definition(device_a), tags=[]),
            Workflow(id=str(uuid4()), project_id="owned", name="归档流程", status="archived",
                     definition=definition(device_a), tags=[]),
            Workflow(id=str(uuid4()), project_id="owned", name="非引用文本", status="draft",
                     definition={"nodes": [{"type": "answer", "data": {
                         "label": device_a,
                     }}], "edges": []}, tags=[]),
        ])
        db.add(WorkflowRelease(
            id=str(uuid4()), workflow_id=active_id, project_id="owned", version=1,
            draft_version=1, definition=definition(device_a), definition_digest="b" * 64,
        ))
        db.add(WorkflowRelease(
            id=str(uuid4()), workflow_id=both_id, project_id="owned", version=1,
            draft_version=1, definition=definition(device_a), definition_digest="c" * 64,
        ))
        await db.commit()

    app = FastAPI()
    app.include_router(router, prefix="/v1/workflows")

    async def db_session():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = db_session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response_a = await client.get("/v1/workflows/device-references", params={
            "project_id": "owned", "device_id": device_a,
        })
        next_page = await client.get("/v1/workflows/device-references", params={
            "project_id": "owned", "device_id": device_a, "limit": 1, "offset": 1,
        })
        response_b = await client.get("/v1/workflows/device-references", params={
            "project_id": "owned", "device_id": device_b,
        })
        foreign = await client.get("/v1/workflows/device-references", params={
            "project_id": "foreign", "device_id": device_a,
        })
        invalid = await client.get("/v1/workflows/device-references", params={
            "project_id": "owned", "device_id": "not-a-uuid",
        })
    assert response_a.status_code == 200
    assert response_a.json()["total"] == 3
    assert {row["workflow_id"]: row["source"] for row in response_a.json()["references"]} == {
        active_id: "published", draft_id: "draft", both_id: "both",
    }
    assert next_page.json()["total"] == 3
    assert len(next_page.json()["references"]) == 1
    assert response_b.status_code == 200
    assert response_b.json()["references"] == [{
        "workflow_id": active_id, "name": "订单查询", "source": "draft",
    }]
    assert foreign.json()["total"] == 1
    assert invalid.status_code == 422
    await engine.dispose()
