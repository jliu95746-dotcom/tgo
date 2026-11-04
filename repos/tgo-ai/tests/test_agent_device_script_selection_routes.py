"""Private selection routes accept only trusted service identities."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import agent_device_script_selection as module
from app.config import settings
from app.schemas.agent_device_script_selection import (
    ScriptSelectionRecord, ScriptSelectionState,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["valid", "wrong", "public"])
@pytest.mark.parametrize("operation", ["save", "read", "request_result"])
async def test_selection_private_identity(monkeypatch, identity, operation):
    project, actor, agent, request = [uuid4() for _ in range(4)]
    key, now = uuid4().hex + uuid4().hex, datetime.now(timezone.utc)
    monkeypatch.setattr(settings, "secret_key", key)
    record = ScriptSelectionRecord(
        id=uuid4(), project_id=project, agent_id=agent,
        request_id=request, configured_by=actor, revision=1, enabled=True,
        binding_id=uuid4(), binding_revision=1, report_id=uuid4(),
        review_id=uuid4(), saved_at=now,
        expires_at=now + timedelta(hours=1), selection_digest="a" * 64,
    )
    result = ScriptSelectionState(
        project_id=project, actor_id=actor, agent_id=agent,
        selection=record, status="selected",
    ) if operation == "read" else record
    action = AsyncMock(return_value=result)
    constructor = Mock(return_value=Mock(**{operation: action}))
    monkeypatch.setattr(module, "ScriptSelectionService", constructor)
    app, cases = FastAPI(), object()
    app.include_router(module.router, prefix="/api/v1/agents")
    app.dependency_overrides[module.get_service] = lambda: cases
    headers = {"X-Internal-API-Key": key, "X-Project-Id": str(project),
               "X-Staff-Id": str(actor)}
    if identity == "wrong":
        headers["X-Internal-API-Key"] = "incorrect"
    elif identity == "public":
        headers = {"Authorization": "Bearer public-token"}
    path = f"/api/v1/agents/{agent}/device-script-selection"
    if operation == "request_result":
        path += f"/requests/{request}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test",
    ) as client:
        response = await client.request(
            "POST" if operation == "save" else "GET",
            path + f"?actor_id={uuid4()}&project_id={uuid4()}",
            headers=headers,
            json={"request_id": str(request), "expected_revision": 0,
                  "report_id": str(record.report_id),
                  "review_id": str(record.review_id)}
            if operation == "save" else None,
        )
    assert response.status_code == (200 if identity == "valid" else 403)
    if identity == "valid":
        constructor.assert_called_once_with(cases)
        assert action.await_args.args[:3] == (project, actor, agent)
        assert response.headers["cache-control"] == "no-store"
    else:
        constructor.assert_not_called()
        action.assert_not_awaited()
