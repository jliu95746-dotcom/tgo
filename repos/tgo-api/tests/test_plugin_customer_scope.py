"""Employee plugin and UI calls stay with their original company and customer."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.api.v1.endpoints import chat, plugins, plugin_tools
from app.core.config import settings
from app.core.security import create_access_token
from tests.test_im_access import records as records
from tests.test_visitor_profile_access import profile_api as profile_api, request


@pytest.fixture
def plugin_api(profile_api, monkeypatch):
    app, db, projects, staff, platforms, visitors, unassigned, other, _, _ = profile_api
    app.include_router(plugins.router, prefix="/plugins")
    app.include_router(plugin_tools.router, prefix="/tools")
    app.include_router(chat.router, prefix="/chat")
    calls = {}
    for method, result in [
        ("render_visitor_panels", {"panels": []}),
        (
            "render_chat_toolbar",
            {"template": "card", "data": {"text": "private-result"}},
        ),
        (
            "send_chat_toolbar_event",
            {"action": "show_toast", "data": {"text": "private-result"}},
        ),
        ("execute_tool", {"success": True, "content": "private-result"}),
        ("render_plugin", {"template": "card", "data": {"text": "private-result"}}),
        (
            "send_plugin_event",
            {"action": "show_toast", "data": {"text": "private-result"}},
        ),
    ]:
        calls[method] = AsyncMock(return_value=result)
        monkeypatch.setattr(plugins.plugin_runtime_client, method, calls[method])
    schedule = Mock()
    monkeypatch.setattr(
        chat.chat_service, "schedule_background_ai_interaction", schedule
    )
    token = create_access_token(staff[0].username, projects[0].id, staff[0].role)
    return (
        app,
        db,
        projects,
        staff,
        platforms,
        visitors,
        unassigned,
        other,
        {"Authorization": "Bearer " + token},
        calls,
        schedule,
    )


def input_for(kind, visitor_id):
    if kind == "generic_render":
        return "/plugins/owned/render", {"visitor_id": str(visitor_id)}
    if kind == "generic_event":
        return "/plugins/owned/event", {
            "visitor_id": str(visitor_id),
            "event_type": "button_click",
            "action_id": "look",
        }
    if kind == "tool":
        return "/tools/execute/owned/look", {
            "arguments": {},
            "context": {"visitor_id": str(visitor_id)},
        }
    if kind == "event":
        return "/plugins/chat-toolbar/owned/event", {
            "event_type": "button_click",
            "action_id": "look",
            "extension_type": "chat_toolbar",
            "visitor_id": str(visitor_id),
        }
    if kind == "render":
        return "/plugins/chat-toolbar/owned/render", {"visitor_id": str(visitor_id)}
    return "/plugins/visitor-panel/render", {"visitor_id": str(visitor_id)}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["panel", "render", "event", "tool", "generic_render", "generic_event"]
)
async def test_plugin_rejects_foreign_company_before_private_upstream_call(
    plugin_api, kind
):
    app, _, _, _, _, visitors, _, _, headers, calls, _ = plugin_api
    path, body = input_for(kind, visitors[1].id)
    result = await request(app, "POST", path, json=body, headers=headers)
    assert result.status_code == 404
    assert all(call.await_count == 0 for call in calls.values())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["panel", "render", "event", "tool", "generic_render", "generic_event"]
)
async def test_regular_employee_cannot_operate_an_unassigned_customer(
    plugin_api, kind, monkeypatch
):
    app, _, projects, _, _, visitors, unassigned, _, _, calls, _ = plugin_api
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    headers = {
        "Authorization": "Bearer "
        + create_access_token(unassigned.username, projects[0].id, unassigned.role)
    }
    path, body = input_for(kind, visitors[0].id)
    result = await request(app, "POST", path, json=body, headers=headers)
    assert result.status_code == 404 and all(
        call.await_count == 0 for call in calls.values()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["panel", "render", "generic_render"])
async def test_claimed_visitor_metadata_cannot_override_database_identity(
    plugin_api, kind
):
    app, _, _, _, _, visitors, _, _, headers, calls, _ = plugin_api
    path, body = input_for(kind, visitors[0].id)
    body["visitor"] = {
        "id": str(visitors[0].id),
        "name": "spoofed",
        "email": "forged@example.invalid",
    }
    result = await request(app, "POST", path, json=body, headers=headers)
    assert result.status_code == 200
    call = calls[
        "render_visitor_panels"
        if kind == "panel"
        else "render_plugin"
        if kind == "generic_render"
        else "render_chat_toolbar"
    ]
    payload = call.await_args.args[0 if kind == "panel" else 1]
    assert (
        payload["visitor"]["name"] == "当前访客"
        and payload["visitor"].get("email") != "forged@example.invalid"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["render", "event", "tool", "generic_render", "generic_event"]
)
async def test_revoked_employee_during_plugin_await_does_not_receive_old_result(
    plugin_api, kind
):
    app, db, _, staff, _, visitors, _, _, headers, calls, _ = plugin_api
    method = {
        "render": "render_chat_toolbar",
        "event": "send_chat_toolbar_event",
        "tool": "execute_tool",
        "generic_render": "render_plugin",
        "generic_event": "send_plugin_event",
    }[kind]
    original = calls[method].return_value

    async def revoke(*args, **kwargs):
        staff[0].token_version += 1
        db.commit()
        return original

    calls[method].side_effect = revoke
    path, body = input_for(kind, visitors[0].id)
    result = await request(app, "POST", path, json=body, headers=headers)
    assert result.status_code == 401 and "private-result" not in result.text


@pytest.mark.asyncio
async def test_plugin_cannot_switch_to_another_customer_session(plugin_api):
    app, _, _, _, _, visitors, _, _, headers, calls, _ = plugin_api
    path, body = input_for("render", visitors[0].id)
    body["session_id"] = str(uuid4())
    result = await request(app, "POST", path, json=body, headers=headers)
    assert result.status_code in (400, 404) and all(
        call.await_count == 0 for call in calls.values()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["visitor_channel", "wrong_agent", "foreign_agent"])
async def test_ui_action_cannot_choose_an_unowned_ai_target(
    plugin_api, fault, monkeypatch
):
    app, _, projects, staff, platforms, visitors, _, _, headers, _, schedule = (
        plugin_api
    )
    body = {
        "channel_id": str(platforms[0].agent_id) + "-agent",
        "channel_type": 1,
        "action_name": "look",
        "agent_id": str(platforms[0].agent_id),
    }
    if fault == "visitor_channel":
        body.update(channel_id=str(visitors[0].id) + "-vtr", channel_type=251)
    elif fault == "wrong_agent":
        body["agent_id"] = str(uuid4())
    else:
        from fastapi import HTTPException
        from app.services import channel_access

        monkeypatch.setattr(
            channel_access.ai_client,
            "get_agent",
            AsyncMock(side_effect=HTTPException(404, "not found")),
        )
    result = await request(app, "POST", "/chat/ui/action", json=body, headers=headers)
    assert result.status_code in (400, 404)
    schedule.assert_not_called()


@pytest.mark.asyncio
async def test_ui_action_keeps_original_staff_authority_for_background_reply(
    plugin_api,
):
    app, _, _, _, platforms, _, _, _, headers, _, schedule = plugin_api
    body = {
        "channel_id": str(platforms[0].agent_id) + "-agent",
        "channel_type": 1,
        "action_name": "look",
    }
    result = await request(app, "POST", "/chat/ui/action", json=body, headers=headers)
    assert result.status_code == 200
    snapshot = schedule.call_args.kwargs.get("visitor_chat_access")
    assert snapshot is not None and headers["Authorization"].split(" ", 1)[
        1
    ] not in repr(snapshot)


@pytest.mark.asyncio
async def test_staff_revocation_during_ui_target_lookup_prevents_scheduling(
    plugin_api, monkeypatch
):
    app, db, _, staff, platforms, _, _, _, headers, _, schedule = plugin_api
    from app.services import channel_access

    async def revoke(**kwargs):
        staff[0].token_version += 1
        db.commit()
        return {"id": str(platforms[0].agent_id), "name": "客服"}

    monkeypatch.setattr(channel_access.ai_client, "get_agent", revoke)
    result = await request(
        app,
        "POST",
        "/chat/ui/action",
        json={
            "channel_id": str(platforms[0].agent_id) + "-agent",
            "channel_type": 1,
            "action_name": "look",
        },
        headers=headers,
    )
    assert result.status_code == 401
    schedule.assert_not_called()


@pytest.mark.asyncio
async def test_revoked_original_staff_cannot_start_the_scheduled_reply(plugin_api):
    app, db, _, staff, platforms, _, _, _, headers, _, schedule = plugin_api
    result = await request(
        app,
        "POST",
        "/chat/ui/action",
        json={
            "channel_id": str(platforms[0].agent_id) + "-agent",
            "channel_type": 1,
            "action_name": "look",
        },
        headers=headers,
    )
    assert result.status_code == 200
    access = schedule.call_args.kwargs["visitor_chat_access"]
    staff[0].token_version += 1
    db.commit()
    from app.services.visitor_chat_access import guarded_chat_call
    from app.core.exceptions import TGOAPIException

    operation = AsyncMock()
    with pytest.raises(TGOAPIException):
        await guarded_chat_call(operation, access)
    operation.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["role", "token_version", "visitor_owner"])
async def test_original_staff_is_rechecked_before_response_body(
    plugin_api, change, monkeypatch
):
    _, db, projects, staff, _, visitors, replacement_staff, _, headers, _, _ = (
        plugin_api
    )
    from fastapi.security import HTTPAuthorizationCredentials
    from starlette.responses import Response
    from app.api.current_access_route import CurrentAccessResponse
    from app.core.exceptions import TGOAPIException
    from app.services.profile_access import profile_guard, require_profile_access

    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    if change == "visitor_owner":
        staff[0].role = "user"
        db.commit()
    credential = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=headers["Authorization"].split(" ", 1)[1]
    )
    access = require_profile_access(db, credential, None)
    guard = profile_guard(access, visitor_id=visitors[0].id)
    sent = []

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.start":
            if change == "role":
                staff[0].role = "user"
            elif change == "token_version":
                staff[0].token_version += 1
            else:
                from app.models import VisitorSession

                session = (
                    db.query(VisitorSession)
                    .filter(VisitorSession.visitor_id == visitors[0].id)
                    .one()
                )
                session.staff_id = replacement_staff.id
            db.commit()

    with pytest.raises(TGOAPIException):
        await CurrentAccessResponse(Response("private-result"), guard)(
            {"type": "http"}, AsyncMock(), send
        )
    assert [message["type"] for message in sent] == ["http.response.start"]
