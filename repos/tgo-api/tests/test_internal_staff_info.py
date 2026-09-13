"""Internal staff context must be real, project-scoped and credential-free."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.internal.endpoints import ai_events, users
from app.core.database import get_db
from app.models import Project, Staff
from app.schemas.visitor import VisitorResponse


@pytest.fixture
def staff_app():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Project.__table__.create(engine)
    Staff.__table__.create(engine)
    with Session(engine) as db:
        project = Project(id=uuid4(), name="Fixture", api_key="private-project-key")
        db.add(project)
        db.flush()
        staff = Staff(
            id=uuid4(), project_id=project.id, username="private-login",
            password_hash="private-password-hash", name="测试坐席", nickname="小林",
            description="处理商品咨询", role="agent", status="busy",
            is_active=True, service_paused=True,
        )
        db.add(staff)
        db.commit()
        app = FastAPI()
        app.include_router(users.router, prefix="/internal/users")
        app.include_router(ai_events.router, prefix="/internal/ai/events")
        app.dependency_overrides[get_db] = lambda: db
        yield app, db, project, staff
    engine.dispose()


@pytest.mark.asyncio
async def test_staff_read_returns_persisted_safe_context(staff_app):
    app, db, project, staff = staff_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            f"/internal/users/{staff.id}-staff", params={"project_id": str(project.id)},
        )
        assert response.status_code == 200
        assert response.json() == {
            "id": str(staff.id), "type": "staff", "name": "测试坐席", "nickname": "小林",
            "avatar_url": None, "description": "处理商品咨询", "role": "agent", "status": "busy",
            "is_active": True, "service_paused": True,
        }
        for private in [staff.username, staff.password_hash, project.api_key]:
            assert private not in response.text
        staff.nickname = "小周"
        staff.is_active = False
        db.commit()
        updated = await client.get(
            f"/internal/users/{staff.id}-staff", params={"project_id": str(project.id)},
        )
        assert updated.json()["nickname"] == "小周"
        assert updated.json()["is_active"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["foreign_project", "missing_staff", "deleted_staff", "deleted_project"])
async def test_unavailable_staff_never_returns_placeholder_success(staff_app, case):
    app, db, project, staff = staff_app
    staff_id, project_id = staff.id, project.id
    if case == "foreign_project":
        project_id = uuid4()
    elif case == "missing_staff":
        staff_id = uuid4()
    elif case == "deleted_staff":
        staff.deleted_at = datetime.now(timezone.utc)
    else:
        project.deleted_at = datetime.now(timezone.utc)
    db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            f"/internal/users/{staff_id}-staff", params={"project_id": str(project_id)},
        )
    assert response.status_code == 404
    assert staff.nickname not in response.text
    assert staff.password_hash not in response.text


@pytest.mark.asyncio
async def test_invalid_staff_id_is_rejected(staff_app):
    app, _, project, _ = staff_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/internal/users/not-a-uuid-staff", params={"project_id": str(project.id)})
    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", [
    "manual_service.request", "user_info.update", "user_sentiment.update",
    "visitor_sentiment.update", "user_tag.add", "visitor_tag.add",
])
async def test_visitor_only_events_on_staff_fail_without_mutating_staff(staff_app, event_type):
    app, db, _, staff = staff_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/internal/ai/events", json={
            "event_type": event_type, "user_id": f"{staff.id}-staff",
            "payload": {"visitor": {"name": "不应写入"}},
        })
    assert response.status_code == 400
    assert response.json()["detail"] == "该操作仅支持访客，不支持员工账户"
    db.refresh(staff)
    assert staff.name == "测试坐席"


def test_internal_staff_schema_is_explicit_and_route_is_not_public(staff_app):
    from app.main import app as public_app

    app, _, _, _ = staff_app
    schema = app.openapi()["components"]["schemas"]["InternalStaffResponse"]
    assert schema["properties"]["type"]["const"] == "staff"
    assert "password_hash" not in schema["properties"]
    assert "/internal/users/{user_id}" not in public_app.openapi()["paths"]


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["-vtr", ""])
async def test_visitor_read_keeps_existing_profile_contract(staff_app, monkeypatch, suffix):
    app, _, project, _ = staff_app
    visitor_id = uuid4()
    now = datetime.now(timezone.utc)
    payload = VisitorResponse(
        id=visitor_id, project_id=project.id, platform_id=uuid4(),
        platform_open_id="fixture-visitor", name="客户", nickname="顾客甲",
        created_at=now, updated_at=now, first_visit_time=now, last_visit_time=now,
        is_online=True, service_status="active",
    )

    def get_visitor(db, requested_id, requested_project):
        assert requested_id == visitor_id and requested_project == project.id
        return SimpleNamespace(platform=None)

    monkeypatch.setattr(users, "_get_visitor_with_relations", get_visitor)
    monkeypatch.setattr(users, "_build_enriched_visitor_payload", lambda **kwargs: payload)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            f"/internal/users/{visitor_id}{suffix}", params={"project_id": str(project.id)},
        )
    assert response.status_code == 200
    assert response.json()["platform_open_id"] == "fixture-visitor"
    assert response.json()["service_status"] == "active"
    assert response.json()["recent_activities"] == []
    assert "type" not in response.json()
