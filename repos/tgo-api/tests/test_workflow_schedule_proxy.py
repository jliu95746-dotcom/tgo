"""Real gateway routes preserve the original actor across an upstream wait."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import workflow_schedules as routes
from app.core.database import Base, get_db
from app.core.security import get_current_active_user
from app.models.project import Project
from app.models.staff import Staff
from app.schemas.workflow_schedule import ScheduleReceipt, ScheduleState


@dataclass
class Harness:
    client: httpx.AsyncClient
    db: Session
    staff: Staff
    project: Project


@pytest_asyncio.fixture
async def harness() -> AsyncIterator[Harness]:
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(
        engine, tables=[Project.__table__, Staff.__table__]
    )
    with Session(engine, expire_on_commit=False) as db:
        project = Project(id=uuid4(), name="定时计划合成企业", api_key=uuid4().hex)
        db.add(project)
        db.flush()
        staff = Staff(
            id=uuid4(),
            project_id=project.id,
            username="schedule-test-" + uuid4().hex[:12],
            password_hash="disabled-test-login",
            role="admin",
            account_enabled=True,
            token_version=1,
        )
        db.add(staff)
        db.commit()
        application = FastAPI()
        application.include_router(routes.router, prefix="/v1/ai/workflows")
        application.dependency_overrides[get_db] = lambda: db
        application.dependency_overrides[
            get_current_active_user
        ] = lambda: staff
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://test",
        ) as client:
            yield Harness(client, db, staff, project)
    engine.dispose()


def payload() -> dict[str, object]:
    return {
        "request_id": str(uuid4()),
        "expected_revision": 0,
        "expected_release_id": str(uuid4()),
        "expected_release_digest": "a" * 64,
        "enabled": False,
    }


def receipt(harness: Harness, request: dict[str, object]) -> ScheduleReceipt:
    now = datetime.now(timezone.utc)
    return ScheduleReceipt.model_validate(
        {
            "request_id": request["request_id"],
            "operation": "save",
            "replayed": False,
            "schedule": {
                "id": str(uuid4()),
                "project_id": str(harness.project.id),
                "workflow_id": "owned-flow",
                "revision": 1,
                "enabled": False,
                "release_id": request["expected_release_id"],
                "release_digest": request["expected_release_digest"],
                "cron_expression": "0 9 * * *",
                "timezone": "UTC",
                "next_run_at": None,
                "created_by": str(harness.staff.id),
                "updated_by": str(harness.staff.id),
                "created_at": now,
                "updated_at": now,
            },
        }
    )


@pytest.mark.asyncio
async def test_save_uses_authenticated_tenant_actor_and_no_store(
    harness: Harness, monkeypatch
) -> None:
    request = payload()
    upstream = AsyncMock(return_value=receipt(harness, request))
    monkeypatch.setattr(routes.workflow_client, "save_schedule", upstream)
    response = await harness.client.put(
        "/v1/ai/workflows/owned-flow/schedule",
        json=request,
        params={"project_id": str(uuid4())},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    args = upstream.await_args.args
    assert args[:3] == ("owned-flow", harness.project.id, harness.staff.id)
    assert str(args[3].request_id) == response.json()["request_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["project_id", "actor_staff_id", "next_run_at", "cron_expression"]
)
async def test_client_cannot_override_actor_clock_or_published_rule(
    harness: Harness, monkeypatch, field: str
) -> None:
    upstream = AsyncMock()
    monkeypatch.setattr(routes.workflow_client, "save_schedule", upstream)
    request = payload()
    request[field] = "untrusted"
    response = await harness.client.put(
        "/v1/ai/workflows/owned-flow/schedule", json=request
    )
    assert response.status_code == 422
    upstream.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_admin_cannot_read_or_modify_any_schedule_route(
    harness: Harness, monkeypatch
) -> None:
    harness.staff.role = "user"
    harness.db.commit()
    upstream = AsyncMock()
    for method in (
        "get_schedule",
        "save_schedule",
        "disable_schedule",
        "get_schedule_command",
    ):
        monkeypatch.setattr(routes.workflow_client, method, upstream)
    endpoints = [
        ("GET", "/schedule", None),
        ("PUT", "/schedule", payload()),
        (
            "POST",
            "/schedule/disable",
            {"request_id": str(uuid4()), "expected_revision": 1},
        ),
        ("GET", "/schedule/commands/" + str(uuid4()), None),
    ]
    for method, suffix, body in endpoints:
        result = await harness.client.request(
            method, "/v1/ai/workflows/owned-flow" + suffix, json=body
        )
        assert result.status_code == 403
    upstream.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["role", "disabled", "token", "staff_deleted", "project_deleted"]
)
async def test_authority_revoked_during_wait_does_not_return_committed_result(
    harness: Harness, monkeypatch, change: str
) -> None:
    request = payload()
    result = receipt(harness, request)

    async def revoke(*args):
        if change == "role":
            harness.staff.role = "user"
        elif change == "disabled":
            harness.staff.account_enabled = False
        elif change == "token":
            harness.staff.token_version += 1
        elif change == "staff_deleted":
            harness.staff.deleted_at = datetime.now(timezone.utc)
        else:
            harness.project.deleted_at = datetime.now(timezone.utc)
        harness.db.commit()
        return result

    upstream = AsyncMock(side_effect=revoke)
    monkeypatch.setattr(routes.workflow_client, "save_schedule", upstream)
    response = await harness.client.put(
        "/v1/ai/workflows/owned-flow/schedule", json=request
    )
    assert response.status_code == 403
    assert "schedule" not in response.json()
    upstream.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_cached_admin_is_rejected_before_upstream(
    harness: Harness, monkeypatch
) -> None:
    harness.staff.account_enabled = False
    harness.db.commit()
    upstream = AsyncMock(
        return_value=ScheduleState(
            schedule=None, binding_current=False, execution_available=False
        )
    )
    monkeypatch.setattr(routes.workflow_client, "get_schedule", upstream)
    response = await harness.client.get("/v1/ai/workflows/owned-flow/schedule")
    assert response.status_code == 403
    upstream.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_evidence_survives_gateway_without_internal_probe_data(
    harness: Harness, monkeypatch,
) -> None:
    state = ScheduleState.model_validate({
        "schedule": None, "binding_current": False, "execution_available": False,
        "runtime": {
            "status": "database_unavailable", "ready": False,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "scheduler_recent": True, "worker_recent": True,
            "database_ready": False, "instance_id": "private-test-instance",
        },
    })
    upstream = AsyncMock(return_value=state)
    monkeypatch.setattr(routes.workflow_client, "get_schedule", upstream)
    response = await harness.client.get("/v1/ai/workflows/owned-flow/schedule")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    result = response.json()["runtime"]
    assert result["status"] == "database_unavailable"
    assert result["scheduler_recent"] and result["worker_recent"]
    assert not result["database_ready"] and not result["ready"]
    assert "instance_id" not in result


@pytest.mark.asyncio
async def test_client_supplies_internal_key_and_server_owned_ids(
    harness: Harness,
    monkeypatch,
) -> None:
    from app.core.config import settings
    from app.schemas.workflow_schedule import ScheduleSave
    from app.services.workflow_client import WorkflowServiceClient

    key = uuid4().hex + uuid4().hex
    monkeypatch.setattr(settings, "SECRET_KEY", key)
    request = payload()
    result = receipt(harness, request)
    client = WorkflowServiceClient()
    upstream = AsyncMock(
        return_value=httpx.Response(
            200,
            json=result.model_dump(mode="json"),
        )
    )
    monkeypatch.setattr(client, "_make_request", upstream)
    saved = await client.save_schedule(
        "owned-flow",
        harness.project.id,
        harness.staff.id,
        ScheduleSave.model_validate(request),
    )
    assert saved == result
    kwargs = upstream.await_args.kwargs
    assert kwargs["extra_headers"]["X-Internal-API-Key"] == key
    assert kwargs["json_data"]["project_id"] == str(harness.project.id)
    assert kwargs["json_data"]["actor_staff_id"] == str(harness.staff.id)


def test_main_router_keeps_the_existing_configuration_policy() -> None:
    from app.api.v1.router import api_router
    from app.api.company_configuration_access import (
        require_configuration_write,
    )

    scheduled = [
        route for route in api_router.routes if "/schedule" in route.path
    ]
    assert len(scheduled) == 6
    assert all(
        require_configuration_write
        in [dependency.dependency for dependency in route.dependencies]
        for route in scheduled
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [None, "role", "disabled", "project_deleted", "expired", "token_reissued"],
)
async def test_private_timer_authority_checks_current_membership(
    harness: Harness, monkeypatch, change
):
    from app.api.internal.endpoints import workflow_schedules as private
    from app.core.config import settings

    key = uuid4().hex + uuid4().hex
    monkeypatch.setattr(settings, "SECRET_KEY", key)
    monkeypatch.setattr(
        private,
        "new_service_available",
        lambda db, project: change != "expired",
    )
    if change == "role":
        harness.staff.role = "user"
    if change == "disabled":
        harness.staff.account_enabled = False
    if change == "project_deleted":
        harness.project.deleted_at = datetime.now(timezone.utc)
    if change == "token_reissued":
        harness.staff.token_version += 1
    harness.db.commit()
    application = FastAPI()
    application.include_router(
        private.router, prefix="/internal/workflows/schedules"
    )
    application.dependency_overrides[get_db] = lambda: harness.db
    body = {
        "project_id": str(harness.project.id),
        "actor_staff_id": str(harness.staff.id),
        "workflow_id": "owned-flow",
        "schedule_id": str(uuid4()),
        "revision": 1,
        "occurrence_id": str(uuid4()),
        "scheduled_at": datetime.now(timezone.utc).isoformat(),
        "release_id": str(uuid4()),
        "release_digest": "a" * 64,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post(
            "/internal/workflows/schedules/authority/verify", json=body
        )
        assert response.status_code == 403
        response = await client.post(
            "/internal/workflows/schedules/authority/verify",
            json=body,
            headers={"X-Internal-API-Key": key},
        )
    assert response.status_code == (
        200 if change in {None, "token_reissued"} else 409
    )
    if change in {None, "token_reissued"}:
        assert response.json()["occurrence_id"] == body["occurrence_id"]
        assert response.headers["cache-control"] == "no-store"
