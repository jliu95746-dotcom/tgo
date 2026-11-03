"""Conflict responses keep their status through the application's real handler."""

import httpx
import pytest
from fastapi import FastAPI

from app.exceptions import ConflictError, TGOAIServiceException
from app.main import tgo_ai_service_exception_handler


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", [None, "agent_activation_policy"])
async def test_conflict_status_preserves_generic_and_resource_codes(resource):
    app = FastAPI()
    app.add_exception_handler(TGOAIServiceException, tgo_ai_service_exception_handler)

    @app.get("/policy")
    async def conflicting_policy():
        raise ConflictError("岗位配置已有新的版本，请重新读取", resource=resource)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/policy")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == (
        "AGENT_ACTIVATION_POLICY_CONFLICT" if resource else "CONFLICT"
    )
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
