"""Plugin management and credentials require current project authority."""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from fastapi import FastAPI
import httpx
import pytest
from sqlalchemy.orm import Session

from app.api.v1.endpoints import plugins
from app.core.database import get_db
from app.core.exceptions import TGOAPIException, tgo_api_exception_handler
from app.core.security import create_access_token
from app.services import visitor_chat_access
from tests.test_im_access import records as base_records

records = base_records

PRIVATE = "synthetic-plugin-admin-private-352"
SOURCE_URL = f"https://source.test/plugin.yml?token={PRIVATE}"
MANAGEMENT = [
    ("POST", "/fetch-info", "fetch_plugin_info"),
    ("POST", "/install", "install_plugin"),
    ("POST", "/install-stream", "stream"),
    ("GET", "/fixture/check-update", "check_plugin_update"),
    ("POST", "/fixture/upgrade", "stream"),
    ("DELETE", "/fixture", "uninstall_plugin"),
    ("POST", "/fixture/start", "start_plugin"),
    ("POST", "/fixture/stop", "stop_plugin"),
    ("POST", "/fixture/restart", "restart_plugin"),
    ("GET", "/fixture/logs", "get_plugin_logs"),
    ("POST", "/dev-token", "token"),
]


@pytest.fixture
def plugin_admin_api(records, monkeypatch):
    db, projects, staff, _, _ = records
    actor = staff[0]
    app = FastAPI()
    app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    app.include_router(plugins.router, prefix="/plugins")
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(
        visitor_chat_access,
        "SessionLocal",
        lambda: Session(db.get_bind()),
    )
    now = datetime.now(timezone.utc).isoformat()
    installed = {
        "plugin_id": "fixture",
        "name": "Fixture",
        "version": "1",
        "status": "stopped",
        "installed_at": now,
        "updated_at": now,
        "source_url": SOURCE_URL,
    }
    configuration = {
        "id": "fixture",
        "name": "Fixture",
        "version": "2",
        "source": {"binary": {"url": "https://source.test/fixture.zip"}},
        "source_url": SOURCE_URL,
        "runtime": {"env": {"TOKEN": PRIVATE}},
    }
    catalog = {"plugins": [installed], "total": 1}
    results = {
        "list_installed_plugins": catalog,
        "fetch_plugin_info": configuration,
        "install_plugin": installed,
        "check_plugin_update": {
            "has_update": True,
            "current_version": "1",
            "latest_version": "2",
            "latest_config": configuration,
        },
        "get_plugin_logs": {"plugin_id": "fixture", "logs": ["ready"]},
        **{
            method: {"success": True, "message": "ready"}
            for method in [
                "uninstall_plugin",
                "start_plugin",
                "stop_plugin",
                "restart_plugin",
            ]
        },
    }
    calls = {}
    for method, value in results.items():
        calls[method] = AsyncMock(return_value=value)
        monkeypatch.setattr(
            plugins.plugin_runtime_client, method, calls[method]
        )
    streams = []

    async def progress(path, payload, **kwargs):
        streams.append((path, payload, kwargs))
        yield b'data: {"stage":"complete"}\n\n'

    monkeypatch.setattr(plugins, "stream_plugin_progress", progress)
    headers = {
        "Authorization": "Bearer "
        + create_access_token(
            actor.username,
            actor.project_id,
            role="admin",
        )
    }
    return (
        app,
        db,
        actor,
        projects,
        headers,
        calls,
        streams,
        catalog,
        configuration,
    )


def body_for(path, actor, configuration, foreign):
    if path == "/fetch-info":
        return {"url": "https://source.test/plugin.yml"}
    if path.startswith("/install"):
        return {**configuration, "project_id": str(foreign)}
    if path.endswith("/upgrade"):
        return {"latest_config": configuration}
    if path == "/dev-token":
        return {"project_id": str(actor.project_id)}
    return None


async def send(state, method, path, *, authenticated=True):
    app, _, actor, projects, headers, _, _, _, configuration = state
    payload = body_for(path, actor, configuration, projects[1].id)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        return await client.request(
            method,
            "/plugins" + path,
            headers=headers if authenticated else {},
            json=payload,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["user", "agent"])
@pytest.mark.parametrize("method,path,upstream", MANAGEMENT)
async def test_non_admin_cannot_use_management_or_read_configuration(
    plugin_admin_api,
    role,
    method,
    path,
    upstream,
):
    _, db, actor, _, _, calls, streams, _, _ = plugin_admin_api
    actor.role = role
    db.commit()
    response = await send(plugin_admin_api, method, path)
    assert response.status_code == 403
    assert PRIVATE not in response.text
    assert not streams and all(
        call.await_count == 0 for call in calls.values()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,upstream", MANAGEMENT)
async def test_anonymous_management_has_no_upstream_effects(
    plugin_admin_api,
    method,
    path,
    upstream,
):
    response = await send(plugin_admin_api, method, path, authenticated=False)
    assert response.status_code in {401, 403}
    assert not plugin_admin_api[6]
    assert all(call.await_count == 0 for call in plugin_admin_api[5].values())


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,upstream", MANAGEMENT)
async def test_admin_management_preserves_payload_and_project(
    plugin_admin_api,
    method,
    path,
    upstream,
):
    _, _, actor, _, _, calls, streams, _, configuration = plugin_admin_api
    response = await send(plugin_admin_api, method, path)
    assert response.status_code == 200, response.text
    if upstream == "stream":
        assert len(streams) == 1
        endpoint, payload, options = streams[0]
        if path == "/install-stream":
            assert endpoint == "/plugins/install-stream"
            assert payload["project_id"] == str(actor.project_id)
            assert payload["runtime"]["env"]["TOKEN"] == PRIVATE
        else:
            assert endpoint == "/plugins/fixture/upgrade"
            assert options["params"] == {"project_id": str(actor.project_id)}
            assert payload["latest_config"]["source_url"] == SOURCE_URL
    elif upstream == "token":
        assert "token" in response.json()
    elif path == "/fetch-info":
        calls[upstream].assert_awaited_once_with(
            "https://source.test/plugin.yml",
        )
        assert response.json()["runtime"]["env"]["TOKEN"] == PRIVATE
    elif path == "/install":
        payload = calls[upstream].await_args.args[0]
        assert payload["project_id"] == str(actor.project_id)
        assert payload["source_url"] == SOURCE_URL
    else:
        assert calls[upstream].await_args.kwargs["project_id"] == str(
            actor.project_id
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["user", "agent", "admin"])
async def test_catalog_hides_only_non_admin_source_without_mutating_upstream(
    plugin_admin_api,
    role,
):
    _, db, actor, _, _, calls, _, catalog, _ = plugin_admin_api
    actor.role = role
    db.commit()
    before = deepcopy(catalog)
    response = await send(plugin_admin_api, "GET", "/installed")
    assert response.status_code == 200, response.text
    item = response.json()["plugins"][0]
    assert item["plugin_id"] == "fixture" and item["name"] == "Fixture"
    assert item["source_url"] == (SOURCE_URL if role == "admin" else None)
    if role != "admin":
        assert PRIVATE not in response.text
    assert catalog == before
    calls["list_installed_plugins"].assert_awaited_once_with(
        project_id=str(actor.project_id),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,upstream",
    [
        ("/installed", "list_installed_plugins"),
        ("/fetch-info", "fetch_plugin_info"),
        ("/fixture/check-update", "check_plugin_update"),
        ("/install", "install_plugin"),
    ],
)
async def test_revocation_during_upstream_wait_blocks_private_result(
    plugin_admin_api,
    path,
    upstream,
):
    _, db, actor, _, _, calls, _, _, _ = plugin_admin_api
    result = calls[upstream].return_value

    async def revoke(*args, **kwargs):
        actor.role = "user"
        db.commit()
        return result

    calls[upstream].side_effect = revoke
    method = (
        "GET" if path in {"/installed", "/fixture/check-update"} else "POST"
    )
    response = await send(plugin_admin_api, method, path)
    assert response.status_code == 401
    assert PRIVATE not in response.text


@pytest.mark.asyncio
async def test_revoked_stream_does_not_deliver_later_private_chunk(
    plugin_admin_api,
    monkeypatch,
):
    _, db, actor, _, _, _, _, _, _ = plugin_admin_api

    async def progress(*args, **kwargs):
        yield b'data: {"stage":"preparing"}\n\n'
        actor.role = "user"
        db.commit()
        yield f'data: {{"message":"{PRIVATE}"}}\n\n'.encode()

    monkeypatch.setattr(plugins, "stream_plugin_progress", progress)
    response = await send(plugin_admin_api, "POST", "/install-stream")
    assert response.status_code == 200
    assert "preparing" in response.text and PRIVATE not in response.text
    await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["disabled", "revision", "company", "deleted"]
)
@pytest.mark.parametrize(
    "path,upstream",
    [
        ("/installed", "list_installed_plugins"),
        ("/fetch-info", "fetch_plugin_info"),
    ],
)
async def test_credential_changes_during_wait_stop_private_results(
    plugin_admin_api,
    path,
    upstream,
    change,
):
    _, db, actor, projects, _, calls, _, _, _ = plugin_admin_api
    result = calls[upstream].return_value

    async def revoke(*args, **kwargs):
        if change == "disabled":
            actor.account_enabled = False
        elif change == "revision":
            actor.token_version += 1
        elif change == "company":
            actor.project_id = projects[1].id
        else:
            actor.deleted_at = datetime.now(timezone.utc)
        db.commit()
        return result

    calls[upstream].side_effect = revoke
    response = await send(
        plugin_admin_api,
        "GET" if path == "/installed" else "POST",
        path,
    )
    assert response.status_code == 401
    assert PRIVATE not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["user", "agent", "admin"])
@pytest.mark.parametrize(
    "path,upstream,payload",
    [
        ("", "list_plugins", {"plugins": [], "total": 0}),
        ("/chat-toolbar/buttons", "get_chat_toolbar_buttons", {"buttons": []}),
    ],
)
async def test_daily_plugin_catalog_remains_available_for_staff(
    plugin_admin_api,
    monkeypatch,
    role,
    path,
    upstream,
    payload,
):
    _, db, actor, _, _, _, _, _, _ = plugin_admin_api
    actor.role = role
    db.commit()
    call = AsyncMock(return_value=payload)
    monkeypatch.setattr(plugins.plugin_runtime_client, upstream, call)
    response = await send(plugin_admin_api, "GET", path)
    assert response.status_code == 200
    assert response.json() == payload
    call.assert_awaited_once_with(project_id=str(actor.project_id))


@pytest.mark.asyncio
async def test_captured_staff_cannot_be_substituted_for_another_token_holder(
    plugin_admin_api,
):
    from app.core.security import get_current_active_user

    app, _, actor, projects, headers, calls, _, _, _ = plugin_admin_api
    replacement = next(
        item
        for item in app.dependency_overrides[get_db]().query(type(actor))
        if item.project_id == projects[1].id
    )
    app.dependency_overrides[get_current_active_user] = lambda: replacement
    try:
        response = await send(plugin_admin_api, "POST", "/fetch-info")
    finally:
        app.dependency_overrides.pop(get_current_active_user)
    assert response.status_code == 401
    assert all(call.await_count == 0 for call in calls.values())
