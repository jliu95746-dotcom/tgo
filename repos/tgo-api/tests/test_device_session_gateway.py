"""Staff can read only their project's safe device session metadata."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from jose import jwt

from app.api.v1.endpoints import device_control
from app.core.config import settings
from app.core.exceptions import TGOAPIException, tgo_api_exception_handler
from app.core.security import get_current_active_user


@pytest.mark.asyncio
@pytest.mark.parametrize("detail", [False, True])
@pytest.mark.parametrize(
    "case",
    ["valid", "foreign", "invalid", "mismatch", "unavailable", "redirect"],
)
async def test_project_scoped_gateway(monkeypatch, detail, case):
    project, foreign, device, session = (uuid4() for _ in range(4))
    summary = {
        "id": str(session),
        "device_id": str(device),
        "device_name": "测试设备",
        "agent_id": str(uuid4()),
        "agent_name": "测试员工",
        "status": "interrupted",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "ended_at": None,
        "lease_expires_at": None,
        "actions_count": 3,
        "failed_actions_count": 1,
        "screenshots_count": 0,
        "raw_tool_result": "private content",
    }
    if case == "mismatch":
        summary["id" if detail else "device_id"] = str(uuid4())
    seen = []

    async def upstream(request):
        seen.append(request)
        claims = jwt.decode(
            request.headers["Authorization"][7:],
            settings.SECRET_KEY,
            algorithms=["HS256"],
            audience="tgo-device-control",
            issuer="tgo-internal",
        )
        assert claims["sub"] == "tgo-api"
        assert claims["project_id"] == str(project)
        assert "session_id" not in claims and "device_id" not in claims
        assert str(foreign) not in str(request.url)
        assert request.url.path == "/v1/sessions" + (
            f"/{session}" if detail else ""
        )
        if case == "foreign":
            return httpx.Response(
                404, json={"detail": "private upstream error"}
            )
        if case == "unavailable":
            raise httpx.ConnectError("private upstream error", request=request)
        if case == "redirect":
            return httpx.Response(
                307, headers={"location": "https://untrusted.invalid"}
            )
        if case == "invalid":
            return httpx.Response(200, json={"unexpected": True})
        if detail:
            assert request.url.params["step_skip"] == "100"
            assert request.url.params["step_limit"] == "100"
            body = {**summary, "steps": [], "step_total": 3}
        else:
            assert request.url.params["device_id"] == str(device)
            assert request.url.params["skip"] == "20"
            assert request.url.params["limit"] == "20"
            body = {"sessions": [summary], "total": 1}
        return httpx.Response(200, json=body)

    original = httpx.AsyncClient

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(upstream), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    app = FastAPI()
    app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    app.include_router(device_control.router, prefix="/v1/device-control")
    app.dependency_overrides[
        get_current_active_user
    ] = lambda: SimpleNamespace(project_id=project)
    path = (
        f"/v1/device-control/sessions/{session}"
        if detail
        else "/v1/device-control/sessions"
    )
    params = {"project_id": str(foreign)}
    params.update(
        {"step_skip": "100", "step_limit": "100"}
        if detail
        else {
            "device_id": str(device),
            "skip": "20",
            "limit": "20",
        }
    )
    async with original(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as staff:
        response = await staff.get(path, params=params)
    assert len(seen) == 1
    assert "private" not in response.text
    assert response.status_code == (
        200 if case == "valid" else 404 if case == "foreign" else 502
    )
    if case == "valid":
        data = response.json() if detail else response.json()["sessions"][0]
        assert data["status"] == "interrupted"
        assert "raw_tool_result" not in data


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["limit=101", "skip=-1", "device_id=bad"])
async def test_invalid_pagination_never_calls_upstream(monkeypatch, query):
    from unittest.mock import AsyncMock

    client = device_control.device_control_client
    method = AsyncMock()
    monkeypatch.setattr(client, "list_sessions", method)
    app = FastAPI()
    app.include_router(device_control.router, prefix="/v1/device-control")
    app.dependency_overrides[
        get_current_active_user
    ] = lambda: SimpleNamespace(project_id=uuid4())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as staff:
        response = await staff.get("/v1/device-control/sessions?" + query)
    assert response.status_code == 422
    method.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_routes_require_staff_authentication(monkeypatch):
    from unittest.mock import AsyncMock

    method = AsyncMock()
    monkeypatch.setattr(
        device_control.device_control_client, "list_sessions", method
    )
    app = FastAPI()
    app.include_router(device_control.router, prefix="/v1/device-control")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as staff:
        response = await staff.get("/v1/device-control/sessions")
    assert response.status_code == 403
    method.assert_not_awaited()
