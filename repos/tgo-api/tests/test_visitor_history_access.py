"""Public history endpoints must reject keys/IDs without current possession."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.endpoints import visitors as endpoint
from app.core.database import get_db
from app.core.exceptions import TGOAPIException, tgo_api_exception_handler
from app.schemas.wukongim import WuKongIMChannelMessageSyncResponse, WuKongIMMessage
from app.services.im_access import issue_visitor_im_token
from tests.test_im_access import records as records


@pytest.fixture
def history_api(records, monkeypatch):
    db, projects, staff, platforms, visitors = records
    message = WuKongIMMessage(
        header={"no_persist": 0, "red_dot": 0, "sync_once": 0},
        setting=0,
        message_id=1,
        client_msg_no="owned-message",
        message_seq=1,
        from_uid="fixture-staff",
        channel_id=f"{visitors[0].id}-vtr",
        channel_type=251,
        timestamp=1,
        payload={"type": 1, "content": "private fixture history"},
    )
    result = WuKongIMChannelMessageSyncResponse(
        start_message_seq=0, end_message_seq=1, more=0, messages=[message]
    )
    rpc = AsyncMock(return_value=result)
    reconcile = AsyncMock(side_effect=lambda data, **kwargs: data)
    monkeypatch.setattr(endpoint.wukongim_client, "sync_channel_messages", rpc)
    monkeypatch.setattr(endpoint, "reconcile_reply_history", reconcile)
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/visitors")
    app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    app.dependency_overrides[get_db] = lambda: db
    body = {
        "platform_api_key": platforms[0].api_key,
        "channel_id": f"{visitors[0].id}-vtr",
        "channel_type": 251,
    }
    token = issue_visitor_im_token(visitors[0], timedelta(hours=1))
    return (
        app,
        db,
        platforms,
        visitors,
        body,
        {"Authorization": "Bearer " + token},
        rpc,
        reconcile,
    )


@pytest.mark.asyncio
async def test_known_key_and_channel_without_a_grant_do_not_query_history(history_api):
    app, _, _, _, body, _, rpc, _ = history_api
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post("/visitors/messages/sync", json=body)
    assert result.status_code == 401
    assert "private fixture history" not in result.text
    rpc.assert_not_awaited()


@pytest.mark.asyncio
async def test_owned_history_checks_current_actor_and_uses_its_login_uid(history_api):
    app, _, _, visitors, body, headers, rpc, reconcile = history_api
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            "/visitors/messages/sync", json=body, headers=headers
        )
    assert result.status_code == 200
    assert result.headers.get("cache-control") == "no-store"
    assert rpc.await_args.kwargs["login_uid"] == f"{visitors[0].id}-vtr"
    assert "private fixture history" in result.text
    reconcile.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("factor", ["other_visitor", "expired", "retired"])
async def test_wrong_expired_or_retired_grants_never_query_history(history_api, factor):
    app, db, _, visitors, body, headers, rpc, _ = history_api
    if factor == "other_visitor":
        headers = {
            "Authorization": "Bearer "
            + issue_visitor_im_token(visitors[1], timedelta(hours=1))
        }
    elif factor == "expired":
        headers = {
            "Authorization": "Bearer "
            + issue_visitor_im_token(visitors[0], timedelta(seconds=-1))
        }
    else:
        visitors[0].access_retired_at = datetime.now(UTC)
        db.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            "/visitors/messages/sync", json=body, headers=headers
        )
    assert result.status_code == 401
    rpc.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["im_query", "reconcile"])
async def test_an_async_scope_change_prevents_the_queried_history_from_being_released(
    history_api, phase
):
    app, db, _, visitors, body, headers, rpc, reconcile = history_api
    original = rpc.return_value

    def retire():
        visitors[0].access_revision += 1
        db.commit()

    async def query(**kwargs):
        retire()
        return original

    async def restore(data, **kwargs):
        retire()
        return data

    if phase == "im_query":
        rpc.side_effect = query
    else:
        reconcile.side_effect = restore
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            "/visitors/messages/sync", json=body, headers=headers
        )
    assert result.status_code == 401
    assert "private fixture history" not in result.text
    if phase == "im_query":
        reconcile.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_channel_in_an_upstream_batch_is_never_released(history_api):
    app, _, _, visitors, body, headers, rpc, _ = history_api
    rpc.return_value.messages[0].channel_id = f"{visitors[1].id}-vtr"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            "/visitors/messages/sync", json=body, headers=headers
        )
    assert result.status_code == 502
    assert "private fixture history" not in result.text
