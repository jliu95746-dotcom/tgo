"""Exercise the public WebSocket route with a controlled IM transport."""

import asyncio
import inspect
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest
import httpx
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from starlette.websockets import WebSocketDisconnect
from starlette.requests import Request

from app.api.v1.endpoints.wukongim import router
from app.core.config import settings
from app.services import im_gateway
from tests.test_im_access import (  # noqa: F401
    records,
    staff_token,
    visitor_token,
)


class Upstream:
    def __init__(self):
        self.responses = asyncio.Queue()
        self.sent = []
        self.deliveries = []

    async def send(self, raw):
        frame = json.loads(raw)
        self.sent.append(frame)
        if frame["method"] == "connect":
            await self.responses.put(
                json.dumps({"id": frame["id"], "result": {"reasonCode": 1}})
            )
        elif frame["method"] == "send":
            await self.responses.put(
                json.dumps({"id": frame["id"], "result": {"reasonCode": 1}})
            )
        elif frame["method"] == "ping":
            for delivery in self.deliveries:
                await self.responses.put(json.dumps(delivery))
            self.deliveries.clear()
            await self.responses.put(json.dumps({"method": "pong"}))

    async def recv(self):
        return await self.responses.get()


@pytest.fixture
def gateway(records, monkeypatch):  # noqa: F811
    db, companies, staff, platforms, visitors = records
    upstream = Upstream()

    @asynccontextmanager
    async def connect(*args, **kwargs):
        yield upstream

    connector = Mock(side_effect=connect)
    monkeypatch.setattr(im_gateway, "connect", connector)
    monkeypatch.setattr(
        im_gateway.wukongim_client, "register_or_login_user", AsyncMock()
    )
    monkeypatch.setattr(
        im_gateway, "SessionLocal", sessionmaker(bind=db.get_bind())
    )
    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(
        settings, "SAAS_IM_UPSTREAM_URL", "ws://im.internal:5200"
    )
    monkeypatch.setattr(
        settings,
        "SAAS_IM_PUBLIC_URL",
        "wss://support.example.test/api/v1/wukongim/ws",
    )
    app = FastAPI()
    app.include_router(router, prefix="/v1/wukongim")
    # Starlette 0.27 supplies both app and its own ASGI transport. HTTPX 0.28
    # removed only the unused app shortcut; preserve the explicit transport.
    original_init = httpx.Client.__init__
    if "app" not in inspect.signature(original_init).parameters:

        def compatible_init(self, *args, **kwargs):
            kwargs.pop("app", None)
            original_init(self, *args, **kwargs)

        monkeypatch.setattr(httpx.Client, "__init__", compatible_init)
    with TestClient(app) as client:
        yield client, upstream, connector, records


def login(socket, uid, token):
    socket.send_json(
        {
            "id": "login",
            "method": "connect",
            "params": {
                "uid": uid,
                "token": token,
                "deviceId": "____device",
                "deviceFlag": 1,
            },
        }
    )
    return socket.receive_json()


def send(socket, channel_id, channel_type=1):
    socket.send_json(
        {
            "id": "send",
            "method": "send",
            "params": {
                "channelId": channel_id,
                "channelType": channel_type,
                "payload": "e30=",
                "clientMsgNo": "synthetic-message",
            },
        }
    )
    return socket.receive_json()


def test_route_never_returns_direct_im_address_in_saas(gateway):
    client, _, connector, _ = gateway
    response = client.get("/v1/wukongim/route", params={"uid": "example"})
    assert response.status_code == 200
    assert response.json()["tcp_addr"] == ""
    assert response.json()["wss_addr"] == settings.SAAS_IM_PUBLIC_URL
    connector.assert_not_called()


def test_visitor_route_uses_private_gateway_without_saas(gateway, monkeypatch):
    client, _, _, (_, _, _, _, visitors) = gateway
    monkeypatch.setattr(settings, "SAAS_ENABLED", False)
    result = client.get("/v1/wukongim/route", params={"uid": f"{visitors[0].id}-vtr"})
    assert result.status_code == 200
    assert result.json()["ws_addr"] == settings.SAAS_IM_PUBLIC_URL
    assert result.json()["tcp_addr"] == ""


@pytest.mark.parametrize("saas", [True, False])
@pytest.mark.parametrize("direction", ["send", "receive"])
def test_gateway_rechecks_retirement_between_frames(gateway, monkeypatch, saas, direction):
    from datetime import datetime, timezone

    client, upstream, _, (db, _, _, _, visitors) = gateway
    monkeypatch.setattr(settings, "SAAS_ENABLED", saas)
    visitor = visitors[0]
    uid, token = f"{visitor.id}-vtr", visitor_token(visitor)
    with client.websocket_connect("/v1/wukongim/ws") as socket:
        assert login(socket, uid, token)["result"]["reasonCode"] == 1
        assert upstream.sent[0]["params"]["token"] != token
        visitor.access_retired_at = datetime.now(timezone.utc)
        db.commit()
        with pytest.raises(WebSocketDisconnect):
            if direction == "send":
                send(socket, uid, 251)
            else:
                upstream.responses.put_nowait(json.dumps({"method": "recv", "params": {
                    "channelId": uid, "channelType": 251, "messageId": "closed-result",
                    "messageSeq": 1, "header": {}, "payload": "e30=",
                }}))
                socket.receive_json()
    assert not any(item["method"] == "send" for item in upstream.sent)


def test_forged_connect_does_not_reach_upstream(gateway, caplog):
    client, _, connector, (_, _, staff, _, _) = gateway
    token = staff_token(staff[0])
    with client.websocket_connect("/v1/wukongim/ws") as socket:
        response = login(socket, f"{staff[1].id}-staff", token)
        assert response["error"]["code"] == 401
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()
    connector.assert_not_called()
    im_gateway.wukongim_client.register_or_login_user.assert_not_awaited()
    failure = next(
        record
        for record in caplog.records
        if record.message == "Realtime handshake failed"
    )
    assert failure.stage == "identity"
    assert failure.error_type == "TGOAPIException"
    assert failure.elapsed_ms >= 0
    assert token not in str(failure.__dict__)
    assert str(staff[1].id) not in str(failure.__dict__)


def test_cross_company_send_is_blocked_before_im(gateway):
    client, upstream, _, (_, _, staff, _, visitors) = gateway
    with client.websocket_connect("/v1/wukongim/ws") as socket:
        assert login(socket, f"{staff[0].id}-staff", staff_token(staff[0]))[
            "result"
        ]
        assert upstream.sent[0]["params"]["deviceId"] != "____device"
        im_gateway.wukongim_client.register_or_login_user.assert_awaited_once()
        assert upstream.sent[0]["params"]["token"] != staff_token(staff[0])
        denied = send(socket, f"{staff[1].id}-staff")
        assert denied["error"]["code"] == 403
        assert len(upstream.sent) == 1
        allowed = send(socket, f"{visitors[0].id}-vtr", 251)
        assert allowed["result"]["reasonCode"] == 1
        assert (
            upstream.sent[-1]["params"]["channelId"] == f"{visitors[0].id}-vtr"
        )


def test_stale_foreign_delivery_is_filtered_and_acked(gateway):
    client, upstream, _, (_, _, staff, _, visitors) = gateway

    def delivery(visitor, number):
        return {
            "method": "recv",
            "params": {
                "channelId": f"{visitor.id}-vtr",
                "channelType": 251,
                "messageId": str(number),
                "messageSeq": number,
                "header": {},
                "payload": "e30=",
            },
        }

    upstream.deliveries = [delivery(visitors[1], 1), delivery(visitors[0], 2)]
    with client.websocket_connect("/v1/wukongim/ws") as socket:
        login(socket, f"{staff[0].id}-staff", staff_token(staff[0]))
        socket.send_json({"method": "ping"})
        received = socket.receive_json()
        assert received["params"]["messageId"] == "2"
        assert socket.receive_json()["method"] == "pong"
        assert any(
            item["method"] == "recvack" and item["params"]["messageId"] == "1"
            for item in upstream.sent
        )


def test_disabled_visitor_channel_closes_existing_socket(gateway):
    client, upstream, _, (db, _, _, platforms, visitors) = gateway
    with client.websocket_connect("/v1/wukongim/ws") as socket:
        login(socket, f"{visitors[0].id}-vtr", visitor_token(visitors[0]))
        platforms[0].is_active = False
        db.commit()
        socket.send_json({"method": "ping"})
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 1008
        assert len(upstream.sent) == 1


def test_disabled_gateway_and_missing_config_fail_closed(gateway, monkeypatch):
    client, _, connector, _ = gateway
    monkeypatch.setattr(settings, "SAAS_IM_PUBLIC_URL", "")
    assert (
        client.get("/v1/wukongim/route", params={"uid": "example"}).status_code
        == 503
    )
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/v1/wukongim/ws"):
            pass
    monkeypatch.setattr(settings, "SAAS_ENABLED", False)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/v1/wukongim/ws"):
            pass
    connector.assert_not_called()


def test_unknown_methods_and_unseen_ack_cannot_reach_im(gateway):
    client, upstream, _, (_, _, staff, _, _) = gateway
    with client.websocket_connect("/v1/wukongim/ws") as socket:
        login(socket, f"{staff[0].id}-staff", staff_token(staff[0]))
        socket.send_json(
            {
                "id": "unauthorized",
                "method": "subscribe",
                "params": {"channelId": "foreign"},
            }
        )
        assert socket.receive_json()["error"]["code"] == 403
        socket.send_json(
            {
                "method": "recvack",
                "params": {"messageId": "unseen", "messageSeq": 123},
            }
        )
        socket.send_json({"method": "ping"})
        assert socket.receive_json()["method"] == "pong"
        assert [frame["method"] for frame in upstream.sent] == [
            "connect",
            "ping",
        ]


def test_idle_revocation_closes_without_waiting_for_client_message(
    gateway, monkeypatch
):
    client, _, _, (db, _, _, platforms, visitors) = gateway
    monkeypatch.setattr(im_gateway, "REVALIDATE_SECONDS", 0.02)
    with client.websocket_connect("/v1/wukongim/ws") as socket:
        login(socket, f"{visitors[0].id}-vtr", visitor_token(visitors[0]))
        platforms[0].is_active = False
        db.commit()
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 1008


@pytest.mark.parametrize(
    "url",
    [
        "ws://[",
        "ws://localhost:wrong",
        "ws://localhost:70000",
        "https://localhost",
        "ws://user:secret@localhost",
    ],
)
def test_invalid_config_returns_service_unavailable(monkeypatch, url):
    monkeypatch.setattr(settings, "SAAS_IM_PUBLIC_URL", url)
    monkeypatch.setattr(
        settings, "SAAS_IM_UPSTREAM_URL", "ws://localhost:5200"
    )
    with pytest.raises(HTTPException) as error:
        im_gateway.gateway_urls()
    assert error.value.status_code == 503


def test_frame_limit_counts_utf8_bytes(monkeypatch):
    monkeypatch.setattr(im_gateway, "MAX_FRAME_BYTES", 20)
    with pytest.raises(ValueError):
        im_gateway.parse_frame('{"text":"中文中文中文"}')


@pytest.mark.asyncio
@pytest.mark.parametrize("saas", [True, False])
async def test_registration_returns_credential_registered_with_im(
    records, monkeypatch, saas  # noqa: F811
):
    from app.api.v1.endpoints import visitors as endpoint
    from app.schemas.visitor import VisitorRegisterRequest
    from app.services.im_access import authenticate_im_actor
    from app.models import VisitorAIProfile, VisitorSystemInfo, VisitorTag
    from app.models.business_customer_identity import BusinessCustomerBinding
    from app.models.visitor_business_scope import VisitorBusinessScope
    from app.schemas.visitor_access import VisitorAccessInput
    from app.services.visitor_access import registration_access
    from app.services import visitor_chat_access
    from starlette.responses import Response

    db, _, _, platforms, visitors = records
    # Preserve the real fresh-session authorization guard within the test DB.
    monkeypatch.setattr(
        visitor_chat_access, "SessionLocal", sessionmaker(bind=db.get_bind())
    )
    for model in (VisitorTag, VisitorAIProfile, VisitorSystemInfo, BusinessCustomerBinding, VisitorBusinessScope):
        model.__table__.create(db.get_bind(), checkfirst=True)
    monkeypatch.setattr(settings, "SAAS_ENABLED", saas)
    monkeypatch.setattr(
        endpoint, "notify_visitor_profile_updated", AsyncMock()
    )
    register = AsyncMock()
    monkeypatch.setattr(
        endpoint.wukongim_client, "register_or_login_user", register
    )
    monkeypatch.setattr(
        endpoint.visitor_service, "ensure_visitor_channel", AsyncMock()
    )
    monkeypatch.setattr(
        endpoint.visitor_service,
        "upsert_visitor_system_info",
        Mock(return_value=False),
    )
    request = Request({"type": "http", "headers": []})
    secret, _ = registration_access(db, visitors[0], VisitorAccessInput(), new=True)
    db.commit()
    response = await endpoint.register_visitor(
        request=request,
        response=Response(),
        req=VisitorRegisterRequest(
            platform_api_key=platforms[0].api_key,
            platform_open_id=visitors[0].platform_open_id,
            visitor_access_token=secret,
        ),
        db=db,
        user_language="zh",
    )
    uid = f"{visitors[0].id}-vtr"
    register.assert_awaited_once_with(uid=uid, token=response.im_token.get_secret_value())
    assert (
        authenticate_im_actor(db, uid, response.im_token.get_secret_value()).id
        == visitors[0].id
    )
