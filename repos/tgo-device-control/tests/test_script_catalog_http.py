"""Internal discovery uses signed project and device management scope."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database import get_async_db
from app.main import app
from app.services import device_script_catalog as module


@pytest.mark.asyncio
async def test_script_catalog_http_authority_and_response(monkeypatch):
    key = uuid4().hex + uuid4().hex
    monkeypatch.setattr(settings, "SECRET_KEY", key)
    project, device = uuid4(), uuid4()
    now = datetime.now(timezone.utc)

    def token(bound=device, scope=project):
        claims = {
            "sub": "tgo-api",
            "project_id": str(scope),
            "device_id": str(bound),
            "iat": now,
            "exp": now + timedelta(minutes=1),
            "aud": "tgo-device-control",
            "iss": "tgo-internal",
        }
        return {
            "Authorization": "Bearer "
            + jwt.encode(claims, key, algorithm="HS256")
        }

    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = device

    async def database():
        yield db

    connection = SimpleNamespace(
        agent_id=str(device),
        device_db_id=str(device),
        project_id=str(project),
        connection_instance_id="http-connection",
        writer=SimpleNamespace(is_closing=lambda: False),
        send_request=AsyncMock(
            return_value={"protocol_version": 1, "scripts": []}
        ),
    )
    monkeypatch.setattr(
        module.tcp_connection_manager, "get_connection", lambda _: connection
    )
    previous = app.dependency_overrides.get(get_async_db)
    app.dependency_overrides[get_async_db] = database
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            path = f"/v1/devices/{device}/script-catalog"
            assert (await client.get(path)).status_code == 401
            assert (
                await client.get(path, headers=token(uuid4()))
            ).status_code == 403
            assert (
                await client.get(
                    path, headers=token(), params={"project_id": str(uuid4())}
                )
            ).status_code == 403
            connection.send_request.assert_not_awaited()
            response = await client.get(path, headers=token())
            assert response.status_code == 200, response.text
            assert response.json() == {"protocol_version": 1, "scripts": []}
            connection.send_request.assert_awaited_once_with(
                "scripts/list", {}, timeout=10
            )
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_async_db, None)
        else:
            app.dependency_overrides[get_async_db] = previous
