"""Configuration permissions remain available when old templates disappear."""

from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session

from app.api.internal.endpoints.agent_activation import router
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import validation_exception_handler
from app.models.project import Project
from app.models.staff import Staff
from app.services import agent_activation_requirements


@pytest.fixture
def application(monkeypatch):
    project = Project(id=uuid4(), deleted_at=None)
    actor = Staff(
        id=uuid4(),
        project_id=project.id,
        role="admin",
        account_enabled=True,
        deleted_at=None,
    )
    db = Mock(spec=Session)
    db.get.side_effect = lambda model, identifier, **kwargs: (
        actor
        if model is Staff and identifier == actor.id
        else project
        if model is Project and identifier == project.id
        else None
    )
    app = FastAPI()
    app.include_router(router, prefix="/internal/ai/activation")
    app.dependency_overrides[get_db] = lambda: db
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    monkeypatch.setattr(settings, "SECRET_KEY", "synthetic-actor-private-key")

    def unavailable(*args):
        raise AssertionError("Actor permissions must not read templates")

    monkeypatch.setattr(agent_activation_requirements, "list_templates", unavailable)
    return app, db, actor, project


async def request(
    application, key="synthetic-actor-private-key", extra=None, wrong_actor=False
):
    app, _, actor, project = application
    body = {
        "project_id": str(project.id),
        "requested_by": str(uuid4() if wrong_actor else actor.id),
        **(extra or {}),
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(
            "/internal/ai/activation/actor",
            json=body,
            headers={"X-Internal-API-Key": key} if key else {},
        )


@pytest.mark.asyncio
async def test_actor_permissions_do_not_require_a_template_or_model(application):
    response = await request(application)
    assert response.status_code == 200
    value = response.json()
    assert (
        value["scope"] == "agent_configuration_access" and value["authorized"] is True
    )
    assert value["can_enable"] is False
    checked = datetime.fromisoformat(value["checked_at"].replace("Z", "+00:00"))
    assert (
        checked.tzinfo is not None
        and abs((datetime.now(UTC) - checked).total_seconds()) < 5
    )
    assert set(value) == {
        "project_id",
        "requested_by",
        "scope",
        "authorized",
        "can_enable",
        "checked_at",
    }
    assert all(
        call.kwargs.get("populate_existing") is True
        for call in application[1].get.call_args_list
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "missing_key",
        "wrong_key",
        "wrong_actor",
        "role",
        "disabled",
        "staff_deleted",
        "foreign_project",
        "project_deleted",
    ],
)
async def test_actor_permissions_are_current_and_never_public(application, fault):
    _, _, actor, project = application
    if fault == "role":
        actor.role = "user"
    if fault == "disabled":
        actor.account_enabled = False
    if fault == "staff_deleted":
        actor.deleted_at = datetime.now(UTC)
    if fault == "foreign_project":
        actor.project_id = uuid4()
    if fault == "project_deleted":
        project.deleted_at = datetime.now(UTC)
    response = await request(
        application,
        key=""
        if fault == "missing_key"
        else "wrong"
        if fault == "wrong_key"
        else "synthetic-actor-private-key",
        wrong_actor=fault == "wrong_actor",
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_configuration_actor_rejects_permission_smuggling_without_echo(
    application,
):
    response = await request(
        application, extra={"authorized": True, "credential": "must-not-be-returned"}
    )
    assert response.status_code == 422 and "must-not-be-returned" not in response.text
