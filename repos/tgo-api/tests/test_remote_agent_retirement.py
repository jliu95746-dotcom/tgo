"""Retire non-persisted custom registrations without removing built-in agents."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.endpoints import remote_agents
from app.core.security import get_current_active_user


@pytest.fixture
def remote_app() -> FastAPI:
    app = FastAPI()
    app.include_router(remote_agents.router, prefix="/remote-agents")
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace()
    return app


@pytest.mark.asyncio
async def test_custom_registration_is_not_advertised_or_accepted(
    remote_app: FastAPI, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = AsyncMock(return_value=remote_agents.RemoteAgentConfig(
        agent_id="custom-fixture", name="Fixture",
    ))
    monkeypatch.setattr(remote_agents, "_fetch_remote_agent_info", fetch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=remote_app), base_url="http://test",
    ) as client:
        response = await client.post("/remote-agents", json={
            "base_url": "https://example.test", "agent_id": "custom-fixture",
        })
    assert response.status_code == 405
    fetch.assert_not_awaited()
    assert "post" not in remote_app.openapi()["paths"]["/remote-agents"]


@pytest.mark.asyncio
async def test_delete_never_claims_to_remove_non_persisted_agents(
    remote_app: FastAPI,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=remote_app), base_url="http://test",
    ) as client:
        response = await client.delete("/remote-agents/custom-fixture")
    assert response.status_code == 405
    assert "delete" not in remote_app.openapi()["paths"]["/remote-agents/{agent_id}"]


def test_builtin_read_and_test_routes_remain_available(remote_app: FastAPI) -> None:
    paths = remote_app.openapi()["paths"]
    for path in ["/remote-agents", "/remote-agents/{agent_id}", "/remote-agents/{agent_id}/config"]:
        assert "get" in paths[path]
    assert "post" in paths["/remote-agents/{agent_id}/test"]


@pytest.mark.asyncio
async def test_builtin_configuration_still_returns_real_fetch_result(
    remote_app: FastAPI, monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = remote_agents.settings.DEVICE_CONTROL_AGENT_ID
    fetch = AsyncMock(return_value=remote_agents.RemoteAgentConfig(
        agent_id=agent_id, name="Owned fixture", instructions="Fixture instruction",
        tools=["fixture_tool"], model="fixture-model",
    ))
    monkeypatch.setattr(remote_agents, "_fetch_remote_agent_info", fetch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=remote_app), base_url="http://test",
    ) as client:
        response = await client.get(f"/remote-agents/{agent_id}/config")
    assert response.status_code == 200
    assert response.json()["instructions"] == "Fixture instruction"
    assert response.json()["tools"] == ["fixture_tool"]
    assert response.json()["model"] == "fixture-model"
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_agents_cannot_be_read_or_invoked(
    remote_app: FastAPI, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = AsyncMock()
    monkeypatch.setattr(remote_agents, "_fetch_remote_agent_info", fetch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=remote_app), base_url="http://test",
    ) as client:
        for path in ["/remote-agents/unknown-fixture", "/remote-agents/unknown-fixture/config"]:
            assert (await client.get(path)).status_code == 404
        response = await client.post(
            "/remote-agents/unknown-fixture/test", json={"message": "fixture"},
        )
    assert response.status_code == 404
    fetch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream_status", [200, 503])
async def test_builtin_test_route_preserves_upstream_success_and_failure(
    remote_app: FastAPI, monkeypatch: pytest.MonkeyPatch, upstream_status: int,
) -> None:
    original_client = httpx.AsyncClient
    received = []

    def respond(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(upstream_status, json={"content": "owned fixture result"})

    monkeypatch.setattr(remote_agents.httpx, "AsyncClient", lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(respond),
    ))
    agent_id = remote_agents.settings.DEVICE_CONTROL_AGENT_ID
    async with original_client(
        transport=httpx.ASGITransport(app=remote_app), base_url="http://test",
    ) as client:
        response = await client.post(
            f"/remote-agents/{agent_id}/test", json={"message": "fixture"},
        )
    assert response.status_code == 200
    assert response.json()["success"] is (upstream_status == 200)
    assert len(received) == 1 and received[0].method == "POST"
    assert received[0].url.path.endswith(f"/v1/agents/{agent_id}/runs")
