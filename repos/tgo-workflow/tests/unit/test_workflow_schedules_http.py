"""Private HTTP routes reject callers before touching schedule storage."""

from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import FastAPI
import httpx
from pydantic import SecretStr
import pytest
import pytest_asyncio

from app.api import schedules as routes
from app.config import settings
from app.database import get_db
from tests.unit.test_workflow_schedules import (
    Fixture,
    request,
    schedule_database,
)


@pytest_asyncio.fixture
async def fixture() -> AsyncIterator[Fixture]:
    async with schedule_database() as result:
        yield result


@pytest_asyncio.fixture
async def client(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setattr(
        settings, "SECRET_KEY", SecretStr("synthetic-schedule-key")
    )
    application = FastAPI()
    application.include_router(routes.router, prefix="/v1/workflows")

    async def session():
        async with fixture.sessions() as db:
            yield db

    application.dependency_overrides[get_db] = session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as result:
        yield result


@pytest.mark.asyncio
async def test_private_key_required_for_every_route(
    client: httpx.AsyncClient, fixture: Fixture
) -> None:
    body = request(fixture).model_dump(mode="json")
    body.update(
        project_id=str(fixture.project_id),
        actor_staff_id=str(fixture.actor_id),
    )
    root = "/v1/workflows/" + fixture.workflow_id
    for method, suffix, payload in [
        ("GET", "/schedule", None),
        ("PUT", "/schedule", body),
        (
            "POST",
            "/schedule/disable",
            {
                "request_id": body["request_id"],
                "expected_revision": 1,
                "project_id": body["project_id"],
                "actor_staff_id": body["actor_staff_id"],
            },
        ),
        ("GET", "/schedule/commands/" + str(body["request_id"]), None),
    ]:
        response = await client.request(
            method,
            root + suffix,
            json=payload,
            params={
                "project_id": str(fixture.project_id),
                "actor_staff_id": str(fixture.actor_id),
            },
        )
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_save_replay_get_and_stop_through_actual_routes(
    client: httpx.AsyncClient, fixture: Fixture
) -> None:
    headers = {"X-Internal-API-Key": "synthetic-schedule-key"}
    body = request(fixture).model_dump(mode="json")
    body.update(
        project_id=str(fixture.project_id),
        actor_staff_id=str(fixture.actor_id),
    )
    root = "/v1/workflows/" + fixture.workflow_id + "/schedule"
    saved = await client.put(root, json=body, headers=headers)
    assert saved.status_code == 200
    assert saved.headers["cache-control"] == "no-store"
    replay = await client.put(root, json=body, headers=headers)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    original = await client.get(
        root + "/commands/" + str(body["request_id"]),
        headers=headers,
        params={
            "project_id": str(fixture.project_id),
            "actor_staff_id": str(fixture.actor_id),
        },
    )
    assert original.status_code == 200
    assert original.json()["schedule"] == saved.json()["schedule"]
    current = await client.get(
        root, headers=headers, params={"project_id": str(fixture.project_id)}
    )
    assert (
        current.status_code == 200
        and current.json()["schedule"]["revision"] == 1
    )
    stop = dict(body)
    for key in ("expected_release_id", "expected_release_digest", "enabled"):
        stop.pop(key)
    stop["request_id"] = str(uuid4())
    stop["expected_revision"] = 1
    disabled = await client.post(root + "/disable", json=stop, headers=headers)
    assert disabled.status_code == 200
    assert disabled.json()["schedule"]["revision"] == 2
    assert disabled.json()["schedule"]["enabled"] is False
