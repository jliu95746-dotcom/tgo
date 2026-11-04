"""A public stop request owns one current visitor and its original reply."""

from datetime import timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.endpoints import ai_runs
from app.core.database import get_db
from app.core.exceptions import TGOAPIException, tgo_api_exception_handler
from app.models import Visitor
from app.schemas.ai_runs import ReplyCancelResponse, ReplyRun
from app.services.im_access import issue_visitor_im_token
from tests.test_im_access import records as records


@pytest.fixture
def cancel_api(records, monkeypatch):
    db, projects, _, platforms, visitors = records
    other = Visitor(
        project_id=projects[0].id,
        platform_id=platforms[0].id,
        platform_open_id="other-owned-fixture",
    )
    db.add(other)
    db.commit()
    app = FastAPI()
    app.include_router(ai_runs.router, prefix="/runs")
    app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    app.dependency_overrides[get_db] = lambda: db
    body = {"platform_api_key": platforms[0].api_key, "client_msg_no": "original-reply"}
    headers = {
        "Authorization": "Bearer "
        + issue_visitor_im_token(visitors[0], timedelta(hours=1))
    }
    item = ReplyRun(
        project_id=str(projects[0].id),
        client_msg_no=body["client_msg_no"],
        channel_id=f"{visitors[0].id}-vtr",
        channel_type=251,
    )
    read = AsyncMock(return_value=item)
    cancel = AsyncMock(
        return_value=ReplyCancelResponse(client_msg_no=item.client_msg_no)
    )
    monkeypatch.setattr(ai_runs, "get_reply_run", read)
    monkeypatch.setattr(ai_runs, "cancel_reply", cancel)
    return app, db, platforms, visitors, other, body, headers, read, cancel


@pytest.mark.asyncio
async def test_platform_key_and_known_reply_are_not_stop_authority(cancel_api):
    app, _, _, _, _, body, _, read, cancel = cancel_api
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/runs/cancel-by-client", json=body)
    assert response.status_code == 401
    read.assert_not_awaited()
    cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_visitor_can_only_stop_its_own_reply(cancel_api):
    app, _, platforms, _, _, body, headers, read, cancel = cancel_api
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/runs/cancel-by-client", json=body, headers=headers
        )
    assert response.status_code == 202
    read.assert_awaited_once_with(str(platforms[0].project_id), body["client_msg_no"])
    cancel.assert_awaited_once_with(read.return_value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["visitor", "project", "client_number", "channel_type", "expired"]
)
async def test_foreign_or_expired_identity_never_stops_a_reply(cancel_api, fault):
    app, _, platforms, visitors, other, body, headers, read, cancel = cancel_api
    item = read.return_value
    if fault == "visitor":
        read.return_value = item.model_copy(update={"channel_id": f"{other.id}-vtr"})
    elif fault == "project":
        read.return_value = item.model_copy(
            update={"project_id": str(platforms[1].project_id)}
        )
    elif fault == "client_number":
        read.return_value = item.model_copy(update={"client_msg_no": "another-reply"})
    elif fault == "channel_type":
        read.return_value = item.model_copy(update={"channel_type": 1})
    else:
        headers = {
            "Authorization": "Bearer "
            + issue_visitor_im_token(visitors[0], timedelta(seconds=-1))
        }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/runs/cancel-by-client", json=body, headers=headers
        )
    assert response.status_code in (401, 404)
    cancel.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["lookup", "cancel"])
async def test_cancel_response_does_not_adopt_a_changed_visitor_during_await(
    cancel_api, phase
):
    app, db, _, visitors, _, body, headers, read, cancel = cancel_api

    async def revoke(*args):
        visitors[0].access_revision += 1
        db.commit()
        return read.return_value if phase == "lookup" else cancel.return_value

    if phase == "lookup":
        read.side_effect = revoke
    else:
        cancel.side_effect = revoke
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/runs/cancel-by-client", json=body, headers=headers
        )
    assert response.status_code == 401
    if phase == "lookup":
        cancel.assert_not_awaited()
