"""An approval draft cannot assign another tenant or disabled account."""

from types import SimpleNamespace
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints.ai_workflows import router, workflow_client
from app.api.v1.endpoints.ai_workflows import publish_workflow
from app.api.internal.endpoints.workflow_approvals import router as private_approval_router
from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_active_user
from app.models.project import Project
from app.models.staff import Staff
from app.schemas.workflow_releases import WorkflowPublishRequest


@pytest.mark.asyncio
async def test_create_rejects_foreign_or_disabled_approval_assignee(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Project.__table__.create(engine)
    Staff.__table__.create(engine)
    project_id, foreign_project_id, assignee_id, foreign_id, disabled_id, agent_id, deleted_id = (
        uuid4() for _ in range(7)
    )
    with Session(engine) as db:
        db.add_all([
            Project(id=project_id, name="测试企业", api_key="project-key-a"),
            Project(id=foreign_project_id, name="其他企业", api_key="project-key-b"),
            Staff(id=assignee_id, project_id=project_id, username="staff-a", password_hash="hash", role="user"),
            Staff(id=foreign_id, project_id=foreign_project_id, username="staff-b", password_hash="hash", role="user"),
            Staff(id=disabled_id, project_id=project_id, username="staff-c", password_hash="hash", role="user", account_enabled=False),
            Staff(id=agent_id, project_id=project_id, username="staff-d", password_hash="hash", role="agent"),
            Staff(id=deleted_id, project_id=project_id, username="staff-e", password_hash="hash", role="user", deleted_at=datetime.now(UTC)),
        ])
        db.commit()

    def db_override():
        with Session(engine) as db:
            yield db

    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        id=assignee_id, project_id=project_id,
    )
    create_upstream = AsyncMock()
    update_upstream = AsyncMock()
    monkeypatch.setattr(workflow_client, "create_workflow", create_upstream)
    monkeypatch.setattr(workflow_client, "update_workflow", update_upstream)

    def payload(target_id):
        return {
            "name": "人工确认流程", "nodes": [{
                "id": "approval", "type": "approval", "position": {"x": 0, "y": 0},
                "data": {
                    "type": "approval", "label": "确认", "reference_key": "approval",
                    "approver_staff_id": str(target_id), "summary": "核对只读结果",
                    "expires_in_minutes": 60,
                },
            }], "edges": [],
        }

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            for target in (foreign_id, disabled_id, agent_id, deleted_id):
                response = await client.post("/v1/ai/workflows", json=payload(target))
                assert response.status_code == 422
                response = await client.put(
                    "/v1/ai/workflows/draft-flow",
                    json={"nodes": payload(target)["nodes"]},
                )
                assert response.status_code == 422
            create_upstream.assert_not_awaited()
            update_upstream.assert_not_awaited()
            with Session(engine) as db:
                from app.schemas.ai_workflows import WorkflowNodeInput
                from app.services.workflow_approval_assignee import require_approval_assignees
                node = WorkflowNodeInput.model_validate(payload(assignee_id)["nodes"][0])
                require_approval_assignees([node], db, project_id)
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_private_assignee_check_requires_key_and_current_membership(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Project.__table__.create(engine)
    Staff.__table__.create(engine)
    project_id, foreign_project_id, member_id, foreign_id = (uuid4() for _ in range(4))
    with Session(engine) as db:
        db.add_all([
            Project(id=project_id, name="测试企业", api_key="project-key-a"),
            Project(id=foreign_project_id, name="其他企业", api_key="project-key-b"),
            Staff(id=member_id, project_id=project_id, username="member", password_hash="hash", role="user"),
            Staff(id=foreign_id, project_id=foreign_project_id, username="foreign", password_hash="hash", role="user"),
        ])
        db.commit()

    def db_override():
        with Session(engine) as db:
            yield db

    app = FastAPI()
    app.include_router(private_approval_router, prefix="/internal/workflows/approvals")
    app.dependency_overrides[get_db] = db_override
    monkeypatch.setattr(settings, "SECRET_KEY", "synthetic-internal-key")
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            url = "/internal/workflows/approvals/assignees/verify"
            query = {"project_id": str(project_id), "assignee_ids": [str(member_id)]}
            assert (await client.post(url, json=query)).status_code == 403
            headers = {"X-Internal-API-Key": "synthetic-internal-key"}
            response = await client.post(url, json=query, headers=headers)
            assert response.status_code == 200
            assert response.json() == query
            query["assignee_ids"] = [str(foreign_id)]
            assert (await client.post(url, json=query, headers=headers)).status_code == 409
            with Session(engine) as db:
                member = db.get(Staff, member_id)
                member.account_enabled = False
                db.commit()
            query["assignee_ids"] = [str(member_id)]
            assert (await client.post(url, json=query, headers=headers)).status_code == 409
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_publish_rechecks_an_assignee_disabled_after_draft_save(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Project.__table__.create(engine)
    Staff.__table__.create(engine)
    project_id, admin_id, assignee_id = (uuid4() for _ in range(3))
    with Session(engine) as db:
        db.add(Project(id=project_id, name="测试企业", api_key="project-key-a"))
        db.add_all([
            Staff(id=admin_id, project_id=project_id, username="admin", password_hash="hash", role="admin"),
            Staff(id=assignee_id, project_id=project_id, username="reviewer", password_hash="hash", role="user", account_enabled=False),
        ])
        db.commit()

    get_upstream = AsyncMock(return_value={
        "id": "flow-a", "name": "只读确认流程", "status": "draft", "version": 2,
        "publication_revision": 0, "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "definition": {"nodes": [{
            "id": "approval", "type": "approval", "position": {"x": 0, "y": 0},
            "data": {
                "type": "approval", "label": "确认", "reference_key": "approval",
                "approver_staff_id": str(assignee_id), "summary": "核对只读结果",
                "expires_in_minutes": 60,
            },
        }], "edges": []},
    })
    publish_upstream = AsyncMock(return_value=get_upstream.return_value)
    monkeypatch.setattr(workflow_client, "get_workflow", get_upstream)
    monkeypatch.setattr(workflow_client, "publish_workflow", publish_upstream)
    try:
        with Session(engine) as db:
            with pytest.raises(HTTPException) as error:
                await publish_workflow(
                    "flow-a", WorkflowPublishRequest(expected_draft_version=2, expected_publication_revision=0),
                    current_user=SimpleNamespace(id=admin_id, project_id=project_id), db=db,
                )
            assert error.value.status_code == 422
        publish_upstream.assert_not_awaited()

        with Session(engine) as db:
            reviewer = db.get(Staff, assignee_id)
            reviewer.account_enabled = True
            db.commit()
            result = await publish_workflow(
                "flow-a", WorkflowPublishRequest(expected_draft_version=2, expected_publication_revision=0),
                current_user=SimpleNamespace(id=admin_id, project_id=project_id), db=db,
            )
            assert result.id == "flow-a"
        publish_upstream.assert_awaited_once()
    finally:
        engine.dispose()
