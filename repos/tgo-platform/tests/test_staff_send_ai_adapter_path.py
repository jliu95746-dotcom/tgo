from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.api.v1 import messages


class FakeDb:
    def __init__(self, platform: object) -> None:
        self.platform = platform

    async def scalar(self, _: object) -> object:
        return self.platform


def build_request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/v1/messages/send"})


def build_platform() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        type="wecom",
        is_active=True,
        api_key="platform-key",
        config={
            "corp_id": "corp-id",
            "agent_id": "agent-id",
            "app_secret": "app-secret",
        },
    )


def build_send_request() -> messages.SendMessageRequest:
    return messages.SendMessageRequest(
        platform_api_key="platform-key",
        from_uid="staff-id",
        channel_id=f"{uuid4()}-vtr",
        channel_type=251,
        payload={"type": 1, "content": "你好"},
        client_msg_no="staff-message-id",
    )


@pytest.mark.asyncio
async def test_staff_wecom_text_reuses_ai_dispatcher_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform = build_platform()
    sent_messages: list[object] = []
    sent_content: list[dict[str, object]] = []
    direct_send_calls: list[str] = []

    class FakeAdapter:
        async def send_final(self, content: dict[str, object]) -> None:
            sent_content.append(content)

    async def fake_resolve_open_id(_: str) -> str:
        return "external-user"

    async def fake_resolve_open_kfid(*_: object) -> str:
        return "open-kfid"

    async def fake_select_adapter(message: object, **_: object) -> FakeAdapter:
        sent_messages.append(message)
        return FakeAdapter()

    async def fake_access_token(*_: object, **__: object) -> str:
        direct_send_calls.append("token")
        return "access-token"

    async def fake_direct_send(*_: object, **__: object) -> dict[str, object]:
        direct_send_calls.append("send")
        return {"errcode": 0}

    monkeypatch.setattr(
        messages, "resolve_visitor_platform_open_id", fake_resolve_open_id
    )
    monkeypatch.setattr(messages, "resolve_wecom_open_kfid", fake_resolve_open_kfid)
    monkeypatch.setattr(
        messages, "select_adapter_for_target", fake_select_adapter, raising=False
    )
    monkeypatch.setattr(messages, "wecom_get_access_token", fake_access_token)
    monkeypatch.setattr(messages, "wecom_kf_send_msg", fake_direct_send)

    response = await messages.send_message(
        build_send_request(),
        build_request(),
        FakeDb(platform),
    )

    assert response["ok"] is True
    assert sent_content == [{"text": "你好"}]
    assert direct_send_calls == []
    assert len(sent_messages) == 1
    normalized = sent_messages[0]
    assert normalized.from_uid == "external-user"
    assert normalized.extra["message_id"] == "staff-message-id"
    assert normalized.extra["wecom"] == {
        "is_from_colleague": False,
        "source_type": "wecom_kf",
        "open_kfid": "open-kfid",
        "external_userid": "external-user",
    }


@pytest.mark.asyncio
async def test_missing_wecom_context_returns_actionable_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform = build_platform()

    async def fake_resolve_open_id(_: str) -> str:
        return "external-user"

    async def fake_missing_context(*_: object) -> str:
        raise RuntimeError(
            "No WeCom KF conversation found for visitor; cannot resolve open_kfid"
        )

    monkeypatch.setattr(
        messages, "resolve_visitor_platform_open_id", fake_resolve_open_id
    )
    monkeypatch.setattr(messages, "resolve_wecom_open_kfid", fake_missing_context)

    response = await messages.send_message(
        build_send_request(),
        build_request(),
        FakeDb(platform),
    )

    assert response.status_code == 409
    payload = json.loads(response.body)
    assert payload["error"]["code"] == "WECOM_DELIVERY_CONTEXT_MISSING"
    assert "企业微信客服会话" in payload["error"]["message"]
