"""The exact-run recheck is private and cannot accept body authority overrides."""
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import agent_execution as endpoint
from app.config import settings
from app.database import get_db
from app.schemas.agent_execution import AgentExecutionReference


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "anonymous", "key", "project", "channel", "changed"])
async def test_private_check_requires_current_matching_execution(monkeypatch, fault):
    ref = AgentExecutionReference(project_id=uuid4(), agent_id=uuid4(), channel="web",
        configuration_fingerprint="a" * 64, policy_revision="b" * 64)
    monkeypatch.setattr(settings, "secret_key", "synthetic-private-execution-key")
    loader = AsyncMock(return_value=ref.model_copy(update={"policy_revision": "c" * 64}) if fault == "changed" else ref)
    monkeypatch.setattr(endpoint, "read_reference", loader)
    application = FastAPI()
    application.include_router(endpoint.router, prefix="/api/v1/agents")
    application.dependency_overrides[get_db] = lambda: object()
    headers = {"X-Internal-API-Key": "synthetic-private-execution-key", "X-Project-Id": str(ref.project_id), "X-Execution-Channel": "web"}
    if fault == "anonymous":
        headers = {}
    elif fault == "key":
        headers["X-Internal-API-Key"] = "wrong"
    elif fault == "project":
        headers["X-Project-Id"] = str(uuid4())
    elif fault == "channel":
        headers["X-Execution-Channel"] = "internal"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        result = await client.post("/api/v1/agents/execution-check", json=ref.model_dump(mode="json"), headers=headers)
    if fault is None:
        assert result.status_code == 200 and result.json()["can_enable"] is False
        assert result.json()["reference"] == ref.model_dump(mode="json")
    elif fault == "changed":
        assert result.status_code == 409
    else:
        assert result.status_code == 403
        loader.assert_not_awaited()
