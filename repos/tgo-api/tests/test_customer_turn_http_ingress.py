"""Exercise turn HTTP routes through real chat ownership and durable admission."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from starlette.requests import Request

from app.api.v1.endpoints import chat, customer_turns
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import TGOAPIException, tgo_api_exception_handler
from app.models import ChatFile
from app.models.customer_turn import CustomerTurn, CustomerTurnPart
from app.services import chat_media_service, customer_turn_worker, visitor_chat_access
from app.services.customer_turn_store import TurnStore
from app.services.im_access import issue_visitor_im_token
from app.utils.encoding import build_visitor_channel_id
from tests.test_im_access import records as records


@pytest.fixture
def ingress_api(records, monkeypatch):
    db, _, _, platforms, visitors = records
    for model in (CustomerTurn, CustomerTurnPart, ChatFile):
        model.__table__.create(db.get_bind())
    app = FastAPI()
    app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    app.include_router(customer_turns.router, prefix="/turns")
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(settings, "CUSTOMER_TURNS_ENABLED", True)
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", False)
    monkeypatch.setattr(settings, "SAAS_INTERNAL_TOKEN", SecretStr("fixture-channel-proof"))
    enqueue = AsyncMock()
    monkeypatch.setattr(customer_turn_worker.scheduler, "enqueue", enqueue)
    # Authorization reads actual file metadata, but never accesses file storage.
    monkeypatch.setattr(chat_media_service, "get_storage", lambda: object())
    endpoint = AsyncMock(wraps=chat.chat_completion)
    monkeypatch.setattr(chat, "chat_completion", endpoint)
    return SimpleNamespace(
        app=app, db=db, platforms=platforms, visitors=visitors,
        enqueue=enqueue, endpoint=endpoint,
    )


def body(api, **changes):
    return {
        "api_key": api.platforms[0].api_key,
        "from_uid": api.visitors[0].platform_open_id,
        "message": "查询商品资料",
        "source_message_id": "source-original",
        "channel_id": build_visitor_channel_id(api.visitors[0].id),
        "stream": True,
        **changes,
    }


def headers(api, visitor=0):
    proof = issue_visitor_im_token(api.visitors[visitor], timedelta(hours=1))
    return {"Authorization": "Bearer " + proof}


async def post(api, path, payload, request_headers):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        return await client.post(path, json=payload, headers=request_headers)


@pytest.mark.asyncio
async def test_website_inbound_keeps_request_proof_and_duplicate_receipt(ingress_api):
    api = ingress_api
    proof = headers(api)
    first = await post(api, "/turns/inbound", body(api), proof)
    assert first.status_code == 200, first.text
    receipt = first.json()
    assert receipt["event_type"] == "accepted" and not receipt["duplicate"]
    call = api.endpoint.await_args.kwargs
    assert isinstance(call["request"], Request) and call["db"] is api.db
    assert call["credentials"].credentials == proof["Authorization"][7:]
    assert call["x_saas_service_token"] is None
    assert call["request"].state.visitor_chat_access.visitor_id == api.visitors[0].id
    assert call["req"].stream is False and call["req"].wukongim_only is True
    repeated = await post(api, "/turns/inbound", body(api), proof)
    assert repeated.status_code == 200 and repeated.json()["duplicate"]
    assert repeated.json()["turn_id"] == receipt["turn_id"]
    parts = TurnStore(api.db).parts(UUID(receipt["turn_id"]))
    assert len(parts) == 1 and parts[0].payload["message"] == "查询商品资料"
    assert "api_key" not in parts[0].payload
    assert api.enqueue.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "wrong-proof", "fixture-channel-proof"])
async def test_private_channel_requires_original_service_header(ingress_api, monkeypatch, token):
    api = ingress_api
    api.platforms[0].type = "wecom_kf"
    api.db.commit()
    lookup = AsyncMock(return_value=(api.visitors[0], False))
    monkeypatch.setattr(visitor_chat_access, "get_or_create_channel_visitor", lookup)
    request_headers = {} if token is None else {"X-SaaS-Service-Token": token}
    result = await post(api, "/turns/inbound", body(api), request_headers)
    if token == "fixture-channel-proof":
        assert result.status_code == 200, result.text
        call = api.endpoint.await_args.kwargs
        assert call["x_saas_service_token"] == token
        assert call["request"].state.visitor_chat_access.visitor_id == api.visitors[0].id
        assert api.db.get(CustomerTurn, UUID(result.json()["turn_id"])).external_delivery
        lookup.assert_awaited_once()
    else:
        assert result.status_code == 403, result.text
        assert api.db.query(CustomerTurn).count() == 0
        lookup.assert_not_awaited()
        api.enqueue.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "invalid", "foreign", "revoked", "wrong_channel", "inactive"])
async def test_inbound_rejects_invalid_ownership_before_admission(ingress_api, fault):
    api = ingress_api
    payload, proof = body(api), headers(api)
    if fault == "missing":
        proof = {}
    elif fault == "invalid":
        proof = {"Authorization": "Bearer invalid-proof"}
    elif fault == "foreign":
        proof = headers(api, 1)
    elif fault == "revoked":
        api.visitors[0].access_revision += 1
        api.db.commit()
    elif fault == "wrong_channel":
        payload["channel_id"] = build_visitor_channel_id(api.visitors[1].id)
    else:
        api.platforms[0].is_active = False
        api.db.commit()
    result = await post(api, "/turns/inbound", payload, proof)
    assert result.status_code == 401, result.text
    assert api.db.query(CustomerTurn).count() == 0
    assert api.db.query(CustomerTurnPart).count() == 0
    api.enqueue.assert_not_awaited()


def file_record(api, message_type, *, foreign=False):
    visitor, platform = api.visitors[int(foreign)], api.platforms[int(foreign)]
    row = ChatFile(
        id=uuid4(), project_id=platform.project_id,
        channel_id=build_visitor_channel_id(visitor.id), channel_type=251,
        uploaded_by_platform_id=platform.id, file_name="fixture-media",
        file_path="fixture-media", file_size=4,
        file_type="image/png" if message_type == 2 else "audio/wav",
    )
    api.db.add(row)
    api.db.commit()
    return row


@pytest.mark.asyncio
@pytest.mark.parametrize("message_type", [2, 4])
@pytest.mark.parametrize("private_channel", [False, True])
async def test_media_ready_keeps_owned_message_and_advances_once(
    ingress_api, monkeypatch, message_type, private_channel
):
    api = ingress_api
    payload = body(api, message="", msg_type=message_type, media_pending=True)
    proof = headers(api)
    if private_channel:
        api.platforms[0].type = "wecom_kf"
        api.db.commit()
        monkeypatch.setattr(
            visitor_chat_access, "get_or_create_channel_visitor",
            AsyncMock(return_value=(api.visitors[0], False)),
        )
        proof["X-SaaS-Service-Token"] = "fixture-channel-proof"
    pending = await post(api, "/turns/inbound", payload, proof)
    assert pending.status_code == 200, pending.text
    media = file_record(api, message_type)
    payload.update(
        message=f"/v1/chat/files/{media.id}", media_pending=False,
        media_file_id=str(media.id),
    )
    ready = await post(api, "/turns/media-ready", payload, proof)
    assert ready.status_code == 200, ready.text
    assert ready.json()["turn_id"] == pending.json()["turn_id"]
    assert ready.json()["version"] == 2 and not ready.json()["duplicate"]
    again = await post(api, "/turns/media-ready", payload, proof)
    assert again.status_code == 200 and again.json()["duplicate"]
    assert again.json()["version"] == 2
    parts = TurnStore(api.db).parts(UUID(ready.json()["turn_id"]))
    assert len(parts) == 1 and not parts[0].media_pending
    assert parts[0].payload["media_file_id"] == str(media.id)
    assert isinstance(api.endpoint.await_args.kwargs["request"], Request)
    assert api.endpoint.await_args.kwargs["x_saas_service_token"] == (
        "fixture-channel-proof" if private_channel else None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["unknown_source", "still_pending", "text", "foreign_file", "foreign_holder"])
async def test_media_ready_rejects_invalid_completion_without_changing_turn(ingress_api, fault):
    api = ingress_api
    proof = headers(api)
    payload = body(api, message="", msg_type=2, media_pending=True)
    pending = await post(api, "/turns/inbound", payload, proof)
    assert pending.status_code == 200, pending.text
    media = file_record(api, 2, foreign=fault == "foreign_file")
    payload.update(
        message=f"/v1/chat/files/{media.id}", media_pending=False,
        media_file_id=str(media.id),
    )
    if fault == "unknown_source":
        payload["source_message_id"] = "unregistered"
    elif fault == "still_pending":
        payload["media_pending"] = True
    elif fault == "text":
        payload["msg_type"] = 1
    elif fault == "foreign_holder":
        proof = headers(api, 1)
    result = await post(api, "/turns/media-ready", payload, proof)
    assert result.status_code == {
        "unknown_source": 409, "still_pending": 409, "text": 409,
        "foreign_file": 422, "foreign_holder": 401,
    }[fault], result.text
    row = TurnStore(api.db).get(UUID(pending.json()["turn_id"]))
    assert row.version == 1
    assert len(TurnStore(api.db).parts(row.id)) == 1
    assert TurnStore(api.db).parts(row.id)[0].media_pending
    assert api.enqueue.await_count == 1
