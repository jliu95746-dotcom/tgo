"""Real chat routes refuse locators before forwarding or changing a visitor."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.api.v1.endpoints import chat
from app.core.database import get_db
from app.core.exceptions import TGOAPIException, tgo_api_exception_handler
from app.models import VisitorSession
from app.services import visitor_chat_access
from app.services.im_access import issue_visitor_im_token
from tests.test_im_access import records as records


@pytest.fixture
def chat_api(records, monkeypatch):
    db, _, staff, platforms, visitors = records
    VisitorSession.__table__.create(db.get_bind())
    app = FastAPI()
    app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    app.include_router(chat.router, prefix="/chat")
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(
        visitor_chat_access, "SessionLocal", lambda: Session(db.get_bind())
    )
    lookup = AsyncMock(return_value=(visitors[0], False))
    forward = AsyncMock(return_value="original-source")
    transfer = AsyncMock(
        return_value=SimpleNamespace(success=True, assigned_staff_id=staff[0].id)
    )
    profile = AsyncMock()
    monkeypatch.setattr(chat, "get_or_create_visitor", lookup)
    monkeypatch.setattr(chat.chat_service, "send_user_message_to_wukongim", forward)
    monkeypatch.setattr(chat, "transfer_to_staff", transfer)
    monkeypatch.setattr(chat.wukongim_client, "send_visitor_profile_updated", profile)
    monkeypatch.setattr(chat.chat_service, "is_ai_disabled", lambda *_: True)
    from app.services import storage

    monkeypatch.setattr(
        storage, "get_storage", lambda: SimpleNamespace(resolve_url=lambda value: value)
    )
    bearer = "Bearer " + issue_visitor_im_token(visitors[0], timedelta(hours=1))
    return app, db, platforms, visitors, bearer, lookup, forward, transfer, profile


def payload(platform, visitor, mode):
    if mode == "normal":
        return {
            "api_key": platform.api_key,
            "from_uid": visitor.platform_open_id,
            "message": "owned",
            "msg_type": 3,
            "stream": False,
        }
    return {
        "messages": [{"role": "user", "content": "owned"}],
        "user": visitor.platform_open_id,
        "stream": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["normal", "openai"])
async def test_public_chat_key_and_asserted_visitor_cannot_start_work(chat_api, mode):
    app, _, platforms, visitors, _, lookup, forward, transfer, _ = chat_api
    headers = {"X-Platform-API-Key": platforms[0].api_key}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/chat/completion" + ("s" if mode == "openai" else ""),
            json=payload(platforms[0], visitors[0], mode),
            headers=headers,
        )
    assert response.status_code == 401
    lookup.assert_not_awaited()
    forward.assert_not_awaited()
    transfer.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["normal", "openai"])
async def test_registered_visitor_can_reach_only_its_own_chat(chat_api, mode):
    app, _, platforms, visitors, bearer, lookup, forward, _, _ = chat_api
    headers = {"X-Platform-API-Key": platforms[0].api_key, "Authorization": bearer}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        own = await client.post(
            "/chat/completion" + ("s" if mode == "openai" else ""),
            json=payload(platforms[0], visitors[0], mode),
            headers=headers,
        )
        assert own.status_code in (200, 403)
        forward.reset_mock()
        foreign = payload(platforms[0], visitors[0], mode)
        foreign["user" if mode == "openai" else "from_uid"] = "other-asserted-person"
        denied = await client.post(
            "/chat/completion" + ("s" if mode == "openai" else ""),
            json=foreign,
            headers=headers,
        )
        assert denied.status_code == 401
        forward.assert_not_awaited()
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_channel_does_not_forward_a_valid_visitors_message(chat_api):
    app, _, platforms, visitors, bearer, _, forward, transfer, _ = chat_api
    body = payload(platforms[0], visitors[0], "normal")
    body["channel_id"] = f"{visitors[1].id}-vtr"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/chat/completion", json=body, headers={"Authorization": bearer}
        )
    assert response.status_code == 401
    forward.assert_not_awaited()
    transfer.assert_not_awaited()


@pytest.mark.asyncio
async def test_revocation_during_profile_await_stops_the_next_side_effect(chat_api):
    app, db, platforms, visitors, bearer, _, forward, transfer, _ = chat_api

    async def revoke(*args, **kwargs):
        visitors[0].access_revision += 1
        db.commit()
        return "original-source"

    forward.side_effect = revoke
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/chat/completion",
            json=payload(platforms[0], visitors[0], "normal"),
            headers={"Authorization": bearer},
        )
    assert response.status_code == 401
    transfer.assert_not_awaited()
