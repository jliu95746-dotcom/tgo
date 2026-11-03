"""Signed HTTP management against real isolated policy persistence."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from jose import jwt
from sqlalchemy import select

from app.config import settings
from app.core.database import get_async_db
from app.main import app
from app.models.device_script_policy import DeviceScriptPolicy
from test_script_publication import request
import test_script_publication_database as database_tests

pytestmark = database_tests.pytestmark
script_database = database_tests.script_database


@pytest.mark.asyncio
async def test_signed_http_publication_and_revocation_with_real_database(
    script_database, monkeypatch
):
    sessions, project, device, current, _, _, _ = script_database
    key = uuid4().hex + uuid4().hex
    monkeypatch.setattr(settings, "SECRET_KEY", key)
    now = datetime.now(timezone.utc)

    def token(*, bound=device, owner=project, subject="tgo-api"):
        return {
            "Authorization": "Bearer "
            + jwt.encode(
                dict(
                    sub=subject,
                    project_id=str(owner),
                    device_id=str(bound),
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
            path = f"/v1/devices/{device}/script-policy"
            assert (
                await client.put(path, json=request().model_dump())
            ).status_code == 401
            assert (
                await client.put(
                    path,
                    headers=token(bound=uuid4()),
                    json=request().model_dump(),
                )
            ).status_code == 403
            assert (
                await client.put(
                    path,
                    headers=token(subject="tgo-ai"),
                    json=request().model_dump(),
                )
            ).status_code == 403
            assert (
                await client.get(
                    path, headers=token(), params={"project_id": str(uuid4())}
                )
            ).status_code == 403
            assert (
                await client.get(path, headers=token(owner=uuid4()))
            ).status_code == 404
            assert (
                await client.put(
                    path,
                    headers=token(),
                    json={**request().model_dump(), "command": "anything"},
                )
            ).status_code == 422
            current[0].send_request.assert_not_awaited()
            result = await client.put(
                path, headers=token(), json=request().model_dump()
            )
            assert result.status_code == 200, result.text
            assert (
                result.json()["status"] == "active"
                and result.json()["verified"] is False
            )
            result = await client.get(path, headers=token())
            assert (
                result.status_code == 200
                and result.json()["policy"]["revision"] == 1
            )
            result = await client.post(
                path + "/revoke",
                headers=token(),
                json={"expected_policy_revision": 1},
            )
            assert result.status_code == 200, result.text
            assert (
                result.json()["status"] == "revoked"
                and result.json()["policy"]["revision"] == 2
            )
        async with sessions() as db:
            saved = await db.scalar(select(DeviceScriptPolicy))
            assert saved.device_id == device and saved.project_id == project
            assert not saved.enabled and saved.revoked_at is not None
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_async_db, None)
        else:
            app.dependency_overrides[get_async_db] = previous
