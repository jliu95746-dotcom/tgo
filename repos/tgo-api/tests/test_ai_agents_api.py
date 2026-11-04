"""API contract tests for agent-only AI endpoints."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
from datetime import datetime, timezone
import httpx
import pytest

from app.api.v1.endpoints.ai_agents import ai_client
from app.models.project import Project


@pytest.fixture
def authenticated_staff_for_readiness(authenticated_project: Project, monkeypatch):
    from app.main import app
    from app.core.security import get_current_active_user

    actor = SimpleNamespace(id=uuid4(), project_id=authenticated_project.id)
    monkeypatch.setitem(
        app.dependency_overrides, get_current_active_user, lambda: actor,
    )
    return actor


def test_device_action_readiness_uses_authenticated_project(
    client, authenticated_project: Project, authenticated_staff_for_readiness,
    monkeypatch,
) -> None:
    agent_id, other_project_id = uuid4(), uuid4()
    check = AsyncMock(return_value={
        "agent_id": str(agent_id), "device_id": None,
        "tool_name": "fs_read", "status": "not_bound",
    })
    monkeypatch.setattr(ai_client, "get_device_action_readiness", check, raising=False)
    response = client.get(
        f"/v1/ai/agents/{agent_id}/device-action-readiness",
        params={"project_id": str(other_project_id)},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "not_bound"
    check.assert_awaited_once_with(
        str(authenticated_project.id), str(agent_id),
        requested_by=authenticated_staff_for_readiness.id,
    )


def test_device_action_readiness_rejects_staff_from_another_project(
    client, authenticated_staff_for_readiness, monkeypatch,
) -> None:
    authenticated_staff_for_readiness.project_id = uuid4()
    check = AsyncMock()
    monkeypatch.setattr(ai_client, "get_device_action_readiness", check)

    response = client.get(f"/v1/ai/agents/{uuid4()}/device-action-readiness")

    assert response.status_code == 403
    check.assert_not_awaited()


def test_device_action_readiness_rejects_wrong_agent_receipt(
    client, authenticated_staff_for_readiness, monkeypatch,
) -> None:
    agent_id = uuid4()
    monkeypatch.setattr(ai_client, "get_device_action_readiness", AsyncMock(
        return_value={
            "agent_id": str(uuid4()), "device_id": str(uuid4()),
            "tool_name": "fs_read", "status": "ready",
        }
    ), raising=False)
    response = client.get(f"/v1/ai/agents/{agent_id}/device-action-readiness")
    assert response.status_code == 502


def test_device_action_readiness_rejects_ready_without_device(
    client, authenticated_staff_for_readiness, monkeypatch,
) -> None:
    agent_id = uuid4()
    monkeypatch.setattr(ai_client, "get_device_action_readiness", AsyncMock(
        return_value={
            "agent_id": str(agent_id), "device_id": None,
            "tool_name": "fs_read", "status": "ready",
        }
    ), raising=False)
    response = client.get(f"/v1/ai/agents/{agent_id}/device-action-readiness")
    assert response.status_code == 502


def test_device_action_readiness_returns_verified_policy_digest(
    client, authenticated_staff_for_readiness, monkeypatch,
) -> None:
    agent_id, device_id = uuid4(), uuid4()
    monkeypatch.setattr(ai_client, "get_device_action_readiness", AsyncMock(
        return_value={
            "agent_id": str(agent_id), "device_id": str(device_id),
            "tool_name": "fs_read", "status": "ready",
            "policy_digest": "a" * 64,
        }
    ))
    response = client.get(f"/v1/ai/agents/{agent_id}/device-action-readiness")
    assert response.status_code == 200
    assert response.json()["policy_digest"] == "a" * 64


def test_device_action_readiness_rejects_ready_without_policy_digest(
    client, authenticated_staff_for_readiness, monkeypatch,
) -> None:
    agent_id = uuid4()
    monkeypatch.setattr(ai_client, "get_device_action_readiness", AsyncMock(
        return_value={
            "agent_id": str(agent_id), "device_id": str(uuid4()),
            "tool_name": "fs_read", "status": "ready",
        }
    ))
    response = client.get(f"/v1/ai/agents/{agent_id}/device-action-readiness")
    assert response.status_code == 502


@pytest.mark.asyncio
async def test_device_action_readiness_client_uses_private_staff_identity(
    monkeypatch,
) -> None:
    from app.core.config import settings

    project_id, agent_id, actor_id = uuid4(), uuid4(), uuid4()
    monkeypatch.setattr(settings, "SECRET_KEY", "synthetic-device-readiness-key")
    send = AsyncMock(return_value=httpx.Response(200, json={
        "agent_id": str(agent_id), "device_id": None,
        "tool_name": "fs_read", "status": "not_bound",
    }))
    monkeypatch.setattr(ai_client, "_make_request", send)
    await ai_client.get_device_action_readiness(
        str(project_id), str(agent_id), requested_by=actor_id,
    )
    assert send.await_args.kwargs["extra_headers"] == {
        "X-Internal-API-Key": "synthetic-device-readiness-key",
        "X-Project-Id": str(project_id),
        "X-Staff-Id": str(actor_id),
    }


def test_list_agents_openapi_has_no_team_id_parameter(client) -> None:
    """The public list-agents contract should no longer expose team filters."""

    schema = client.get("/v1/openapi.json").json()
    params = schema["paths"]["/v1/ai/agents"]["get"].get("parameters", [])

    assert all(param["name"] != "team_id" for param in params)


def test_list_agents_does_not_forward_team_id(
    client,
    authenticated_project: Project,
    monkeypatch,
) -> None:
    """Agent listing should only forward agent-centric filters downstream."""

    list_agents_mock = AsyncMock(
        return_value={
            "data": [],
            "pagination": {
                "total": 0,
                "limit": 20,
                "offset": 0,
                "has_next": False,
                "has_prev": False,
            },
        }
    )
    monkeypatch.setattr(ai_client, "list_agents", list_agents_mock)

    response = client.get("/v1/ai/agents", params={"model": "gpt-4o"})

    assert response.status_code == 200
    assert response.json()["data"] == []
    assert "team_id" not in list_agents_mock.await_args.kwargs
    assert list_agents_mock.await_args.kwargs["project_id"] == str(authenticated_project.id)


def test_device_reference_filter_uses_authenticated_project(
    client, authenticated_project: Project, monkeypatch,
) -> None:
    device_id = uuid4()
    mocked = AsyncMock(return_value={
        "data": [], "pagination": {
            "total": 0, "limit": 5, "offset": 0,
            "has_next": False, "has_prev": False,
        },
    })
    monkeypatch.setattr(ai_client, "list_agents", mocked)
    response = client.get("/v1/ai/agents", params={
        "project_id": str(uuid4()), "bound_device_id": str(device_id),
        "limit": 5,
    })
    assert response.status_code == 200
    assert mocked.await_args.kwargs["project_id"] == str(authenticated_project.id)
    assert mocked.await_args.kwargs["bound_device_id"] == device_id


def test_device_reference_list_preserves_bound_id_in_response(
    client, monkeypatch,
) -> None:
    device_id = uuid4()
    now = datetime.now(timezone.utc).isoformat()
    mocked = AsyncMock(return_value={
        "data": [{
            "id": str(uuid4()), "name": "订单员工", "model": "fixture",
            "created_at": now, "updated_at": now, "deleted_at": None,
            "bound_device_id": str(device_id), "tools": [],
            "collections": [], "workflows": [],
        }],
        "pagination": {
            "total": 1, "limit": 5, "offset": 0,
            "has_next": False, "has_prev": False,
        },
    })
    monkeypatch.setattr(ai_client, "list_agents", mocked)
    response = client.get("/v1/ai/agents", params={
        "bound_device_id": str(device_id), "limit": 5,
    })
    assert response.status_code == 200
    assert response.json()["data"][0]["bound_device_id"] == str(device_id)
    schema = client.get("/v1/openapi.json").json()
    assert "bound_device_id" in schema["components"]["schemas"]["AgentWithDetailsResponse"]["properties"]


@pytest.mark.asyncio
async def test_ai_client_forwards_device_reference_filter(monkeypatch) -> None:
    from app.services.ai_client import ai_client as service_client

    request = AsyncMock(return_value=object())
    response = AsyncMock(return_value={"data": [], "pagination": {}})
    monkeypatch.setattr(service_client, "_make_request", request)
    monkeypatch.setattr(service_client, "_handle_response", response)
    device_id = uuid4()
    await service_client.list_agents(str(uuid4()), bound_device_id=device_id, limit=5)
    assert request.await_args.kwargs["params"]["bound_device_id"] == str(device_id)


def test_workflow_release_audit_requires_admin_and_uses_authenticated_project(
    client, authenticated_project: Project, monkeypatch,
) -> None:
    from app.main import app
    from app.api.agent_configuration_access import require_agent_configuration_actor

    report = {
        "data": [],
        "pagination": {
            "total": 0, "limit": 20, "offset": 0,
            "has_next": False, "has_prev": False,
        },
    }
    read_audit = AsyncMock(return_value=report)
    monkeypatch.setattr(ai_client, "get_workflow_release_audit", read_audit, raising=False)
    path = "/v1/ai/agents/workflow-release-audit"

    forbidden = client.get(path)
    assert forbidden.status_code == 403
    read_audit.assert_not_awaited()

    app.dependency_overrides[require_agent_configuration_actor] = (
        lambda: SimpleNamespace(id=uuid4())
    )
    try:
        response = client.get(path, params={
            "project_id": str(uuid4()), "limit": 20, "offset": 0,
        })
    finally:
        app.dependency_overrides.pop(require_agent_configuration_actor, None)

    assert response.status_code == 200
    assert response.json() == report
    read_audit.assert_awaited_once_with(
        project_id=str(authenticated_project.id), limit=20, offset=0,
    )


def test_legacy_workflow_pin_requires_admin_and_forwards_current_scope(
    client, authenticated_project: Project, monkeypatch,
) -> None:
    from app.main import app
    from app.api.agent_configuration_access import require_agent_configuration_actor

    actor_id, agent_id, binding_id = uuid4(), uuid4(), uuid4()
    workflow_id, request_id = str(uuid4()), uuid4()
    receipt = {
        "id": str(uuid4()), "project_id": str(authenticated_project.id),
        "requested_by": str(actor_id), "request_id": str(request_id),
        "agent_id": str(agent_id), "binding_id": str(binding_id),
        "workflow_id": workflow_id, "release_version": 2,
        "release_id": str(uuid4()), "release_digest": "a" * 64,
        "agent_active": True, "binding_enabled": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    pin = AsyncMock(return_value=receipt)
    monkeypatch.setattr(ai_client, "pin_legacy_workflow", pin, raising=False)
    path = f"/v1/ai/agents/{agent_id}/workflows/{workflow_id}/pin-legacy"
    body = {
        "request_id": str(request_id), "expected_binding_id": str(binding_id),
        "release_version": 2, "confirmed_disabled": True,
    }
    assert client.post(path, json=body).status_code == 403
    pin.assert_not_awaited()
    app.dependency_overrides[require_agent_configuration_actor] = (
        lambda: SimpleNamespace(id=actor_id)
    )
    try:
        response = client.post(path, params={"project_id": str(uuid4())}, json=body)
    finally:
        app.dependency_overrides.pop(require_agent_configuration_actor, None)
    assert response.status_code == 200
    assert response.json()["binding_id"] == str(binding_id)
    pin.assert_awaited_once_with(
        project_id=str(authenticated_project.id), agent_id=str(agent_id),
        workflow_id=workflow_id, command=body, requested_by=actor_id,
    )


def test_legacy_workflow_pin_history_is_admin_only(
    client, authenticated_project: Project, monkeypatch,
) -> None:
    from app.main import app
    from app.api.agent_configuration_access import require_agent_configuration_actor

    actor_id, agent_id = uuid4(), uuid4()
    read = AsyncMock(return_value={
        "data": [], "pagination": {
            "total": 0, "limit": 20, "offset": 0,
            "has_next": False, "has_prev": False,
        },
    })
    monkeypatch.setattr(ai_client, "get_workflow_pin_transitions", read, raising=False)
    path = f"/v1/ai/agents/{agent_id}/workflow-pin-transitions"
    assert client.get(path).status_code == 403
    read.assert_not_awaited()
    app.dependency_overrides[require_agent_configuration_actor] = (
        lambda: SimpleNamespace(id=actor_id)
    )
    try:
        response = client.get(path, params={"project_id": str(uuid4())})
    finally:
        app.dependency_overrides.pop(require_agent_configuration_actor, None)
    assert response.status_code == 200
    read.assert_awaited_once_with(
        project_id=str(authenticated_project.id), agent_id=str(agent_id),
        requested_by=actor_id, limit=20, offset=0,
    )


def test_create_agent_rejects_team_id_payload(
    client,
    monkeypatch,
) -> None:
    """Legacy team IDs should be rejected instead of silently ignored."""

    create_agent_mock = AsyncMock()
    monkeypatch.setattr(ai_client, "create_agent", create_agent_mock)
    from app.main import app
    from app.api.agent_configuration_access import require_agent_configuration_actor
    # This test checks the request schema; current actor authorization has its
    # own real dependency tests rather than relying on the old project-only fixture.
    app.dependency_overrides[require_agent_configuration_actor] = lambda: SimpleNamespace(id=uuid4())
    try:
        response = client.post("/v1/ai/agents", json={"name": "Support Agent", "model": "gpt-4o",
            "team_id": "123e4567-e89b-12d3-a456-426614174000"})
    finally:
        app.dependency_overrides.pop(require_agent_configuration_actor, None)

    assert response.status_code == 422
    create_agent_mock.assert_not_awaited()


def test_update_agent_forwards_an_explicit_workflow_release(
    client, authenticated_project: Project, monkeypatch,
) -> None:
    from app.main import app
    from app.api.agent_configuration_access import require_agent_configuration_actor

    agent_id = uuid4()
    workflow_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    update_agent_mock = AsyncMock(return_value={
        "id": str(agent_id), "name": "订单员工",
        "created_at": now, "updated_at": now,
    })
    monkeypatch.setattr(ai_client, "update_agent", update_agent_mock)
    app.dependency_overrides[require_agent_configuration_actor] = (
        lambda: SimpleNamespace(id=uuid4())
    )
    try:
        path = f"/v1/ai/agents/{agent_id}"
        invalid = client.patch(
            path, json={"workflow_release_versions": {workflow_id: True}}
        )
        assert invalid.status_code == 422
        update_agent_mock.assert_not_awaited()
        response = client.patch(
            path, json={"workflow_release_versions": {workflow_id: 2}}
        )
    finally:
        app.dependency_overrides.pop(require_agent_configuration_actor, None)

    assert response.status_code == 200
    assert update_agent_mock.await_args.kwargs["project_id"] == str(
        authenticated_project.id
    )
    assert update_agent_mock.await_args.kwargs["agent_data"]["workflow_release_versions"] == {
        workflow_id: 2
    }
