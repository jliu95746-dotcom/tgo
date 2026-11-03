"""Real reservations and signed reads in owned PostgreSQL schemas."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from jose import jwt
from sqlalchemy import select, func

from app.config import settings
from app.core.database import get_async_db
from app.main import app
from app.models.device_script_execution import DeviceScriptExecution
from app.services.device_script_budget import DeviceScriptBudgetService
from app.services.device_script_execution_store import ScriptExecutionStore
from test_script_execution_store import prepare
from test_script_policy_contract import descriptor
import test_script_publication_database as database_tests

pytestmark = database_tests.pytestmark
script_database = database_tests.script_database


def observe(current):
    conn = current[0]
    original = conn.send_request.side_effect

    async def response(method, params, timeout):
        if method != "scripts/budget/get":
            return await original(method, params, timeout)
        policy = conn.desired_script_policy
        return dict(
            **params,
            grant_digest=policy.grant_digest,
            policy_max_runs=policy.max_runs,
            policy_used=0,
            grant_max_runs=2,
            grant_used=0,
        )

    conn.send_request.side_effect = response


@pytest.mark.asyncio
async def test_unknown_reservation_is_consumed_before_dispatch(
    script_database,
):
    sessions, project, device, current, _, _, _ = script_database
    principal = await prepare(sessions, project, device)
    observe(current)
    async with sessions() as db:
        before = await DeviceScriptBudgetService(db).get(project, device)
    assert before.remaining_runs == 1 and before.server_policy_used == 0
    execution = uuid4()
    async with sessions() as db:
        saved, fresh = await ScriptExecutionStore(db).reserve(
            principal, device, execution, {}, descriptor()
        )
        assert fresh and saved.status == "unknown"
    for _ in range(2):
        async with sessions() as db:
            after = await DeviceScriptBudgetService(db).get(project, device)
            assert after.remaining_runs == 0
            assert after.server_policy_used == after.server_grant_used == 1
            assert after.device_policy_used == after.device_grant_used == 0
            assert not after.can_enable
            assert (
                await db.scalar(
                    select(func.count()).select_from(DeviceScriptExecution)
                )
                == 1
            )


@pytest.mark.asyncio
async def test_signed_budget_route_keeps_project_and_device_scope(
    script_database, monkeypatch
):
    sessions, project, device, current, _, _, _ = script_database
    await prepare(sessions, project, device)
    observe(current)
    key = uuid4().hex + uuid4().hex
    monkeypatch.setattr(settings, "SECRET_KEY", key)
    now = datetime.now(timezone.utc)

    def token(project_id=project, device_id=device):
        return {
            "Authorization": "Bearer "
            + jwt.encode(
                dict(
                    sub="tgo-ai",
                    project_id=str(project_id),
                    device_id=str(device_id),
                    iat=now,
                    exp=now + timedelta(minutes=1),
                    aud="tgo-device-control",
                    iss="tgo-internal",
                ),
                key,
                algorithm="HS256",
            )
        }

    async def database():
        async with sessions() as db:
            yield db

    previous = app.dependency_overrides.get(get_async_db)
    app.dependency_overrides[get_async_db] = database
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            path = f"/v1/devices/{device}/script-budget"
            assert (await client.get(path)).status_code == 401
            assert (
                await client.get(path, headers=token(device_id=uuid4()))
            ).status_code == 403
            assert (
                await client.get(path, headers=token(project_id=uuid4()))
            ).status_code == 404
            assert (
                await client.get(
                    path + f"?project_id={uuid4()}", headers=token()
                )
            ).status_code == 403
            response = await client.get(path, headers=token())
            assert response.status_code == 200, response.text
            assert response.json()["remaining_runs"] == 1
            assert not response.json()["can_enable"]
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_async_db, None)
        else:
            app.dependency_overrides[get_async_db] = previous
