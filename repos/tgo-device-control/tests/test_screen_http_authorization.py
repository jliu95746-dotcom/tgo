"""Screen management keeps the existing signed project and device boundary."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from jose import jwt

from app.config import settings
from app.main import app


@pytest.mark.asyncio
async def test_screen_routes_require_signed_management_authority(monkeypatch):
    # An ephemeral test key, never the configured deployment key.
    key = uuid4().hex + uuid4().hex
    monkeypatch.setattr(settings, "SECRET_KEY", key)
    project, device = uuid4(), uuid4()
    now = datetime.now(timezone.utc)

    def token(subject, bound=device):
        claims = {
            "sub": subject,
            "project_id": str(project),
            "device_id": str(bound),
            "iat": now,
            "exp": now + timedelta(minutes=1),
            "aud": "tgo-device-control",
            "iss": "tgo-internal",
        }
        return {"Authorization": "Bearer " + jwt.encode(claims, key, algorithm="HS256")}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        prefix = f"/v1/devices/{device}"
        for method, suffix, body in (
            ("GET", "/screen-grant", None),
            ("GET", "/screen-policy", None),
            ("PUT", "/screen-policy", {}),
            ("POST", "/screen-policy/revoke", {}),
        ):
            assert (
                await client.request(method, prefix + suffix, json=body)
            ).status_code == 401
        for method, suffix in (
            ("PUT", "/screen-policy"),
            ("POST", "/screen-policy/revoke"),
        ):
            assert (
                await client.request(
                    method, prefix + suffix, json={}, headers=token("tgo-ai")
                )
            ).status_code == 403
        assert (
            await client.get(
                prefix + "/screen-grant", headers=token("tgo-api", uuid4())
            )
        ).status_code == 403
        assert (
            await client.get(
                prefix + "/screen-policy",
                params={"project_id": str(uuid4())},
                headers=token("tgo-api"),
            )
        ).status_code == 403
