from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from starlette.requests import Request

from app.api.v1 import messages


class ContextDb:
    def __init__(self, platform: object, inbox: object | None) -> None:
        self.platform = platform
        self.inbox = inbox
        self.statements: list[object] = []

    async def scalar(self, statement: object) -> object | None:
        self.statements.append(statement)
        return self.platform if len(self.statements) == 1 else self.inbox


def context(platform_type: str) -> tuple[SimpleNamespace, SimpleNamespace]:
    platform = SimpleNamespace(
        id=uuid4(),
        type=platform_type,
        is_active=True,
        api_key="test-key",
        config={"app_id": "test-app", "app_secret": "test-secret"},
    )
    inbox = SimpleNamespace(
        message_id="inbound-message",
        chat_type="p2p",
        chat_id="peer-chat",
        session_webhook="https://example.invalid/reply?access_token=test-secret",
        session_webhook_expired_time=int(time.time() * 1000) + 60000,
        conversation_type="1",
        conversation_id="peer-conversation",
    )
    return platform, inbox


async def invoke(monkeypatch, platform, inbox, *, payload=None):
    resolve = AsyncMock(return_value="external-user")
    adapter = SimpleNamespace(send_final=AsyncMock())
    select_adapter = AsyncMock(return_value=adapter)
    monkeypatch.setattr(messages, "resolve_visitor_platform_open_id", resolve)
    monkeypatch.setattr(messages, "select_adapter_for_target", select_adapter)
    db = ContextDb(platform, inbox)
    response = await messages.send_message(
        messages.SendMessageRequest(
            platform_api_key="test-key",
            from_uid="staff",
            channel_type=251,
            channel_id=f"{uuid4()}-vtr",
            client_msg_no="outbound-message",
            payload=payload
            if payload is not None
            else {"type": 1, "content": "这款有货。"},
        ),
        Request({"type": "http", "method": "POST", "path": "/v1/messages/send"}),
        db,
    )
    return response, db, select_adapter, adapter


@pytest.mark.parametrize("platform_type", ["feishu_bot", "dingtalk_bot"])
async def test_staff_reuses_ai_adapter_with_platform_scoped_latest_inbox(
    monkeypatch, platform_type
):
    platform, inbox = context(platform_type)
    response, db, select_adapter, adapter = await invoke(monkeypatch, platform, inbox)
    assert isinstance(response, dict) and response["ok"] is True
    adapter.send_final.assert_awaited_once_with({"text": "这款有货。"})
    normalized = select_adapter.await_args.args[0]
    assert normalized.from_uid == "external-user"
    assert normalized.platform_id == str(platform.id)
    key = "feishu" if platform_type == "feishu_bot" else "dingtalk"
    expected = inbox.message_id if key == "feishu" else inbox.session_webhook
    field = "message_id" if key == "feishu" else "session_webhook"
    assert normalized.extra[key][field] == expected
    # Never use the staff's outbound ID as the customer's reply target.
    assert normalized.extra[key][field] != "outbound-message"
    query = db.statements[1].compile(dialect=postgresql.dialect())
    assert platform.id in query.params.values()
    assert "external-user" in query.params.values()
    assert "NULLS LAST" in str(query) and "LIMIT" in str(query)


@pytest.mark.parametrize("platform_type", ["feishu_bot", "dingtalk_bot"])
async def test_missing_context_never_claims_delivery(monkeypatch, platform_type):
    platform, _ = context(platform_type)
    response, _, select_adapter, adapter = await invoke(monkeypatch, platform, None)
    assert response.status_code == 409
    assert json.loads(response.body)["error"]["code"] == "DELIVERY_CONTEXT_MISSING"
    select_adapter.assert_not_awaited()
    adapter.send_final.assert_not_awaited()


async def test_expired_dingtalk_webhook_requires_new_customer_message(monkeypatch):
    platform, inbox = context("dingtalk_bot")
    inbox.session_webhook_expired_time = int(time.time() * 1000) - 1
    response, _, select_adapter, _ = await invoke(monkeypatch, platform, inbox)
    assert response.status_code == 409
    assert json.loads(response.body)["error"]["code"] == "DELIVERY_CONTEXT_EXPIRED"
    assert "重新发送" in json.loads(response.body)["error"]["message"]
    select_adapter.assert_not_awaited()
    assert "test-secret" not in response.body.decode()


@pytest.mark.parametrize("platform_type", ["feishu_bot", "dingtalk_bot"])
async def test_ambiguous_group_context_is_not_used_for_private_staff_reply(
    monkeypatch, platform_type
):
    platform, inbox = context(platform_type)
    inbox.chat_type = "group"
    inbox.conversation_type = "2"
    response, _, select_adapter, _ = await invoke(monkeypatch, platform, inbox)
    assert response.status_code == 409
    assert json.loads(response.body)["error"]["code"] == "DELIVERY_CONTEXT_AMBIGUOUS"
    select_adapter.assert_not_awaited()


async def test_missing_feishu_configuration_does_not_fall_back_to_stdout(monkeypatch):
    platform, inbox = context("feishu_bot")
    platform.config = {}
    response, _, select_adapter, _ = await invoke(monkeypatch, platform, inbox)
    assert response.status_code == 400
    assert json.loads(response.body)["error"]["code"] == "PLATFORM_CONFIG_INVALID"
    select_adapter.assert_not_awaited()


@pytest.mark.parametrize("payload", [{"type": "bad"}, {"type": 1, "content": "  "}])
async def test_invalid_message_is_rejected_before_adapter(monkeypatch, payload):
    platform, inbox = context("feishu_bot")
    response, _, select_adapter, _ = await invoke(
        monkeypatch, platform, inbox, payload=payload
    )
    assert response.status_code == 400
    assert json.loads(response.body)["error"]["code"] == "INVALID_PAYLOAD"
    select_adapter.assert_not_awaited()


async def test_unsupported_attachment_is_not_silently_dropped(monkeypatch):
    platform, inbox = context("dingtalk_bot")
    response, _, select_adapter, _ = await invoke(
        monkeypatch,
        platform,
        inbox,
        payload={"type": 3, "url": "https://example.invalid/a.pdf"},
    )
    assert response.status_code == 400
    assert json.loads(response.body)["error"]["code"] == "UNSUPPORTED_MESSAGE_TYPE"
    select_adapter.assert_not_awaited()


@pytest.mark.parametrize("platform_type", ["feishu_bot", "dingtalk_bot"])
@pytest.mark.parametrize("upstream_status", [200, 403])
def test_http_route_uses_real_ai_adapter_and_propagates_provider_failure(
    monkeypatch,
    platform_type,
    upstream_status,
):
    from app.domain.services.adapters import feishu_bot

    platform, inbox = context(platform_type)
    db = ContextDb(platform, inbox)
    app = FastAPI()
    app.include_router(messages.router)
    app.dependency_overrides[messages.get_db] = lambda: db
    monkeypatch.setattr(
        messages,
        "resolve_visitor_platform_open_id",
        AsyncMock(return_value="external-user"),
    )
    monkeypatch.setattr(
        feishu_bot,
        "feishu_get_tenant_access_token",
        AsyncMock(return_value="test-token"),
    )
    outbound: list[httpx.Request] = []

    def provider(request: httpx.Request) -> httpx.Response:
        outbound.append(request)
        body = (
            {"code": 0, "data": {}} if platform_type == "feishu_bot" else {"errcode": 0}
        )
        return httpx.Response(upstream_status, json=body)

    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client_class(
            transport=httpx.MockTransport(provider), **kwargs
        ),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/messages/send",
            json={
                "platform_api_key": "test-key",
                "from_uid": "staff",
                "channel_id": f"{uuid4()}-vtr",
                "channel_type": 251,
                "payload": {"type": 1, "content": "这款有货。"},
                "client_msg_no": "outbound-message",
            },
        )
    assert response.status_code == (200 if upstream_status == 200 else 502)
    assert len(outbound) == 1
    request = outbound[0]
    payload = json.loads(request.content)
    if platform_type == "feishu_bot":
        assert request.url.path.endswith("/messages/inbound-message/reply")
        assert json.loads(payload["content"])["text"] == "这款有货。"
    else:
        assert str(request.url) == inbox.session_webhook
        assert payload["text"]["content"] == "这款有货。"
    assert "test-secret" not in response.text and "test-token" not in response.text
