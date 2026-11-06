import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import httpx
from fastapi import FastAPI
from pydantic import SecretStr

from app.api.internal.endpoints import wecom_native
from app.schemas.wecom_native import NativeContextEvent
from app.services import wecom_native_context


@pytest.mark.asyncio
async def test_private_bridge_rejects_missing_or_invalid_service_identity(
    monkeypatch,
):
    monkeypatch.setattr(
        wecom_native.settings,
        "SAAS_INTERNAL_TOKEN",
        SecretStr("test-only-service-identity"),
    )
    app = FastAPI()
    app.include_router(wecom_native.router, prefix="/internal/wecom-native")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        body = {"platform_id": str(uuid4())}
        assert (
            await client.post("/internal/wecom-native/pending", json=body)
        ).status_code == 403
        assert (
            await client.post(
                "/internal/wecom-native/pending",
                json=body,
                headers={"X-SaaS-Service-Token": "wrong"},
            )
        ).status_code == 403


def test_bridge_is_disabled_for_other_channels_and_deleted_platforms():
    db = SimpleNamespace(
        get=Mock(
            return_value=SimpleNamespace(
                deleted_at=None,
                is_active=True,
                type="website",
                config={"native_handoff_enabled": True},
            )
        )
    )
    with pytest.raises(Exception) as caught:
        wecom_native_context.native_platform(db, uuid4())
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_native_staff_message_uses_staff_role_and_persisted_source_receipt(
    monkeypatch,
):
    platform = SimpleNamespace(id=uuid4(), project_id=uuid4())
    visitor = SimpleNamespace(
        id=uuid4(),
        service_mode="auto",
        ai_disabled=False,
        custom_attributes={},
    )
    query = Mock()
    query.filter.return_value.with_for_update.return_value.first.return_value = (
        visitor
    )
    db = SimpleNamespace(query=Mock(return_value=query), commit=Mock())
    gateway = SimpleNamespace(
        send_visitor_profile_updated=AsyncMock(),
        get_message_by_client_msg_no=AsyncMock(return_value=None),
        send_message=AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        wecom_native_context, "native_platform", lambda *_: platform
    )
    monkeypatch.setattr(
        wecom_native_context, "_exchange_state", AsyncMock(return_value=None)
    )
    monkeypatch.setitem(
        sys.modules,
        "app.services.chat_service",
        SimpleNamespace(resolve_service_mode=lambda _p, v: v.service_mode),
    )
    monkeypatch.setitem(
        sys.modules,
        "app.services.wukongim_client",
        SimpleNamespace(wukongim_client=gateway),
    )
    event = NativeContextEvent(
        platform_id=platform.id,
        external_userid="customer",
        event_id="native-one",
        kind="staff",
        service_state=3,
        servicer_userid="employee-a",
        content="人工已记录你的黑色偏好",
    )
    result = await wecom_native_context.mirror_native_event(db, event)
    assert result.service_mode == "manual"
    assert visitor.ai_disabled is True
    gateway.send_visitor_profile_updated.assert_awaited_once()
    sent = gateway.send_message.call_args.kwargs
    assert sent["from_uid"].endswith("-staff")
    assert sent["payload"]["content"] == event.content
    assert sent["payload"]["extra"]["native_role"] == "staff"
    assert sent["client_msg_no"] == wecom_native_context.message_identity(
        platform.id, "native-one"
    )
    gateway.get_message_by_client_msg_no.return_value = object()
    gateway.send_message.reset_mock()
    again = await wecom_native_context.mirror_native_event(db, event)
    assert again.mirrored is True
    gateway.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_history_failure_is_retried_after_ai_has_already_been_disabled(
    monkeypatch,
):
    platform = SimpleNamespace(id=uuid4(), project_id=uuid4())
    visitor = SimpleNamespace(
        id=uuid4(),
        service_mode="auto",
        ai_disabled=False,
        custom_attributes={},
    )
    query = Mock()
    query.filter.return_value.with_for_update.return_value.first.return_value = (
        visitor
    )
    db = SimpleNamespace(query=Mock(return_value=query), commit=Mock())
    gateway = SimpleNamespace(
        send_visitor_profile_updated=AsyncMock(),
        get_message_by_client_msg_no=AsyncMock(
            side_effect=RuntimeError("history unavailable")
        ),
    )
    monkeypatch.setattr(
        wecom_native_context, "native_platform", lambda *_: platform
    )
    monkeypatch.setattr(
        wecom_native_context, "_exchange_state", AsyncMock(return_value=None)
    )
    monkeypatch.setitem(
        sys.modules,
        "app.services.chat_service",
        SimpleNamespace(resolve_service_mode=lambda _p, v: v.service_mode),
    )
    monkeypatch.setitem(
        sys.modules,
        "app.services.wukongim_client",
        SimpleNamespace(wukongim_client=gateway),
    )
    event = NativeContextEvent(
        platform_id=platform.id,
        external_userid="customer",
        event_id="native-one",
        kind="staff",
        service_state=3,
        servicer_userid="employee-a",
        content="接待中",
    )
    with pytest.raises(RuntimeError, match="history unavailable"):
        await wecom_native_context.mirror_native_event(db, event)
    assert visitor.ai_disabled is True
    db.commit.assert_called_once()
