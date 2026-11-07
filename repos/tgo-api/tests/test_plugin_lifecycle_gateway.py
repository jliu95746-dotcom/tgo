"""Plugin lifecycle retains saved configuration and explicit missing logs."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import plugins
from app.services.plugin_runtime_client import PluginRuntimeClient


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [{"logs": []}, {"logs": ["ready"]}, {}])
async def test_log_payload_is_preserved(monkeypatch, value):
    call = AsyncMock(return_value=value)
    monkeypatch.setattr(plugins.plugin_runtime_client, "get_plugin_logs", call)
    project = uuid4()
    assert (
        await plugins.get_plugin_logs(
            "fixture",
            SimpleNamespace(project_id=project),
        )
        == value
    )
    call.assert_awaited_once_with("fixture", project_id=str(project))


@pytest.mark.asyncio
async def test_missing_or_foreign_plugin_logs_return_not_found(monkeypatch):
    monkeypatch.setattr(
        plugins.plugin_runtime_client,
        "get_plugin_logs",
        AsyncMock(return_value=None),
    )
    with pytest.raises(HTTPException) as error:
        await plugins.get_plugin_logs(
            "missing",
            SimpleNamespace(project_id=uuid4()),
        )
    assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["start", "stop", "restart", "uninstall"])
@pytest.mark.parametrize(
    "result",
    [
        None,
        {"success": False, "message": "busy"},
        {"success": True, "message": "ready"},
    ],
)
async def test_lifecycle_keeps_existing_success_and_failure_contract(
    monkeypatch,
    action,
    result,
):
    method = action + "_plugin"
    call = AsyncMock(return_value=result)
    monkeypatch.setattr(plugins.plugin_runtime_client, method, call)
    project = uuid4()
    actor = SimpleNamespace(project_id=project)
    if result and result["success"]:
        assert await getattr(plugins, method)("fixture", actor) == result
    else:
        with pytest.raises(HTTPException) as error:
            await getattr(plugins, method)("fixture", actor)
        assert error.value.status_code == 400
        if result:
            assert error.value.detail == "busy"
    call.assert_awaited_once_with(
        "fixture",
        *([None] if action == "start" else []),
        project_id=str(project),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("configuration", [None, {}, {"id": "fixture"}])
async def test_start_client_preserves_configuration_and_project(
    monkeypatch,
    configuration,
):
    original = httpx.AsyncClient
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(200, json={"success": True})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(
            **kwargs,
            transport=httpx.MockTransport(reply),
        ),
    )
    client = PluginRuntimeClient()
    client.base_url = "http://runtime.test"
    result = await client.start_plugin("fixture", configuration, "owned")
    assert result == {"success": True} and len(calls) == 1
    request = calls[0]
    assert (
        request.method == "POST"
        and request.url.path == "/plugins/fixture/start"
    )
    assert request.url.params.multi_items() == [("project_id", "owned")]
    assert (
        json.loads(request.content) if request.content else None
    ) == configuration
