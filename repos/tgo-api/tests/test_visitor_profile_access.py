"""Public locators cannot read profiles, AI setup or write another visitor's activity."""

from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.api.v1.endpoints import channels, visitors as endpoints
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import TGOAPIException, tgo_api_exception_handler
from app.core.security import create_access_token
from app.models import ChannelMember, Staff, Visitor, VisitorActivity, VisitorSession
from app.services.im_access import issue_visitor_im_token
from tests.test_im_access import records as records


@pytest.fixture
def profile_api(records, monkeypatch):
    db, projects, staff, platforms, visitors = records
    for model in (ChannelMember, VisitorActivity, VisitorSession):
        model.__table__.create(db.get_bind())
    extra_staff = Staff(
        project_id=projects[0].id,
        username="unassigned",
        role="user",
        password_hash="unused",
    )
    extra_visitor = Visitor(
        project_id=projects[0].id,
        platform_id=platforms[0].id,
        platform_open_id="other-person",
    )
    db.add_all([extra_staff, extra_visitor])
    db.flush()
    db.add(
        VisitorSession(
            project_id=projects[0].id,
            visitor_id=visitors[0].id,
            staff_id=staff[0].id,
            status="open",
        )
    )
    platforms[0].agent_id = uuid4()
    visitors[0].name = "当前访客"
    visitors[0].humanization_skill_name = "private-skill"
    db.commit()
    app = FastAPI()
    app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    app.include_router(channels.router, prefix="/channels")
    app.include_router(endpoints.router, prefix="/visitors")
    app.dependency_overrides[get_db] = lambda: db
    from app.services import visitor_chat_access

    monkeypatch.setattr(
        visitor_chat_access, "SessionLocal", lambda: Session(db.get_bind())
    )
    headers = {
        "X-Platform-API-Key": platforms[0].api_key,
        "Authorization": "Bearer "
        + issue_visitor_im_token(visitors[0], timedelta(hours=1)),
    }
    fetch_agent = AsyncMock(
        return_value={
            "id": str(platforms[0].agent_id),
            "name": "客服",
            "avatar_url": "",
            "instruction": "private-instruction",
            "model": "private-model",
            "config": {"secret": "private-config"},
            "tools": [{"key": "private-tool"}],
        }
    )
    monkeypatch.setattr(channels.ai_client, "get_agent", fetch_agent)
    return (
        app,
        db,
        projects,
        staff,
        platforms,
        visitors,
        extra_staff,
        extra_visitor,
        headers,
        fetch_agent,
    )


async def request(app, method, path, **kwargs):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://owned"
    ) as client:
        return await client.request(method, path, **kwargs)


@pytest.mark.asyncio
async def test_basic_profile_requires_original_owner_before_reading_any_id(profile_api):
    app, _, _, _, _, visitors, _, _, headers, _ = profile_api
    missing = await request(app, "GET", f"/visitors/{visitors[0].id}/basic")
    assert missing.status_code == 401
    foreign = await request(
        app, "GET", f"/visitors/{visitors[1].id}/basic", headers=headers
    )
    assert foreign.status_code in (401, 404)
    own = await request(
        app, "GET", f"/visitors/{visitors[0].id}/basic", headers=headers
    )
    assert own.status_code == 200
    assert own.json()["name"] == "当前访客"
    assert (
        "ai_settings" not in own.json() and "humanization_skill_name" not in own.json()
    )
    assert own.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", ["end", "expired", "revised", "disabled_platform"])
async def test_invalid_profile_holder_is_rejected(profile_api, purpose):
    app, db, _, _, platforms, visitors, _, _, headers, _ = profile_api
    if purpose == "end":
        from app.services.visitor_end_grant import issue_visitor_end_token

        visitors[0].access_secret_digest = "a" * 64
        db.commit()
        headers = {
            **headers,
            "Authorization": "Bearer "
            + issue_visitor_end_token(visitors[0]).get_secret_value(),
        }
    elif purpose == "expired":
        headers = {
            **headers,
            "Authorization": "Bearer "
            + issue_visitor_im_token(visitors[0], timedelta(seconds=-1)),
        }
    elif purpose == "revised":
        visitors[0].access_revision += 1
    else:
        platforms[0].is_active = False
    db.commit()
    result = await request(
        app, "GET", f"/visitors/{visitors[0].id}/basic", headers=headers
    )
    assert result.status_code == 401


@pytest.mark.asyncio
async def test_platform_key_alone_cannot_read_agent_instruction(profile_api):
    app, _, _, _, platforms, _, _, _, _, fetch = profile_api
    result = await request(
        app,
        "GET",
        "/channels/info",
        params={"channel_id": str(platforms[0].agent_id) + "-agent", "channel_type": 1},
        headers={"X-Platform-API-Key": platforms[0].api_key},
    )
    assert result.status_code == 401
    fetch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["own", "staff", "agent"])
async def test_visitor_channel_display_has_only_public_fields(profile_api, target):
    app, _, _, staff, platforms, visitors, _, _, headers, _ = profile_api
    channel_id = {
        "own": str(visitors[0].id) + "-vtr",
        "staff": str(staff[0].id) + "-staff",
        "agent": str(platforms[0].agent_id) + "-agent",
    }[target]
    result = await request(
        app,
        "GET",
        "/channels/info",
        params={
            "channel_id": channel_id,
            "channel_type": 251 if target == "own" else 1,
        },
        headers=headers,
    )
    assert result.status_code == 200
    assert result.json().get("extra") is None
    for value in (
        "private-instruction",
        "private-model",
        "private-config",
        "private-tool",
        "private-skill",
    ):
        assert value not in result.text
    assert result.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target", ["foreign_visitor", "unassigned_staff", "foreign_staff", "other_agent"]
)
async def test_visitor_cannot_lookup_other_customer_or_unrelated_sender(
    profile_api, target
):
    app, _, _, staff, _, _, unassigned, other, headers, fetch = profile_api
    channel_id = {
        "foreign_visitor": str(other.id) + "-vtr",
        "unassigned_staff": str(unassigned.id) + "-staff",
        "foreign_staff": str(staff[1].id) + "-staff",
        "other_agent": str(uuid4()) + "-agent",
    }[target]
    result = await request(
        app,
        "GET",
        "/channels/info",
        params={"channel_id": channel_id, "channel_type": 1},
        headers=headers,
    )
    assert result.status_code in (401, 404)
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_display_rechecks_holder_after_upstream_await(profile_api):
    app, db, _, _, platforms, visitors, _, _, headers, fetch = profile_api
    original = fetch.return_value

    async def revoke(**kwargs):
        visitors[0].access_revision += 1
        db.commit()
        return original

    fetch.side_effect = revoke
    result = await request(
        app,
        "GET",
        "/channels/info",
        params={"channel_id": str(platforms[0].agent_id) + "-agent", "channel_type": 1},
        headers=headers,
    )
    assert result.status_code == 401 and "private-instruction" not in result.text


@pytest.mark.asyncio
async def test_activity_requires_own_grant_and_does_not_modify_platform_setup(
    profile_api,
):
    app, db, _, _, platforms, visitors, _, other, headers, _ = profile_api
    body = {
        "visitor_id": str(visitors[0].id),
        "activity_type": "page_view",
        "title": "当前页",
        "context": {"page_url": "https://untrusted.invalid/page"},
    }
    missing = await request(
        app,
        "POST",
        "/visitors/activities",
        json=body,
        headers={"X-Platform-API-Key": platforms[0].api_key},
    )
    assert missing.status_code == 401 and db.query(VisitorActivity).count() == 0
    foreign = await request(
        app,
        "POST",
        "/visitors/activities",
        json={**body, "visitor_id": str(other.id)},
        headers=headers,
    )
    assert foreign.status_code in (401, 404) and db.query(VisitorActivity).count() == 0
    own = await request(app, "POST", "/visitors/activities", json=body, headers=headers)
    assert own.status_code == 201 and db.query(VisitorActivity).count() == 1
    db.refresh(platforms[0])
    assert not platforms[0].is_used and platforms[0].used_website_url is None


@pytest.mark.asyncio
async def test_activity_id_cannot_update_another_visitor_record(profile_api):
    app, db, projects, _, _, visitors, _, other, headers, _ = profile_api
    item = VisitorActivity(
        project_id=projects[0].id,
        visitor_id=other.id,
        activity_type="page_view",
        title="其他访客",
    )
    db.add(item)
    db.commit()
    body = {
        "id": str(item.id),
        "visitor_id": str(visitors[0].id),
        "activity_type": "page_view",
        "title": "不得覆盖",
    }
    result = await request(
        app, "POST", "/visitors/activities", json=body, headers=headers
    )
    assert result.status_code == 404
    db.refresh(item)
    assert item.title == "其他访客"


@pytest.mark.asyncio
async def test_staff_basic_lookup_stays_scoped_to_original_company(profile_api):
    app, _, projects, staff, _, visitors, _, _, _, _ = profile_api
    headers = {
        "Authorization": "Bearer "
        + create_access_token(staff[0].username, projects[0].id, staff[0].role)
    }
    own = await request(
        app, "GET", f"/visitors/{visitors[0].id}/basic", headers=headers
    )
    assert own.status_code == 200
    foreign = await request(
        app, "GET", f"/visitors/{visitors[1].id}/basic", headers=headers
    )
    assert foreign.status_code == 404


@pytest.mark.asyncio
async def test_agent_display_rejects_an_upstream_reply_for_a_different_agent(
    profile_api,
):
    app, _, _, _, platforms, _, _, _, headers, fetch = profile_api
    fetch.return_value = {"id": str(uuid4()), "name": "wrong-sender", "avatar_url": ""}
    result = await request(
        app,
        "GET",
        "/channels/info",
        params={
            "channel_id": str(platforms[0].agent_id) + "-agent",
            "channel_type": 1,
        },
        headers=headers,
    )
    assert result.status_code == 502 and "wrong-sender" not in result.text


@pytest.mark.asyncio
async def test_unassigned_staff_channel_lookup_cannot_read_customer_details(
    profile_api, monkeypatch
):
    app, _, projects, _, _, visitors, staff, _, _, _ = profile_api
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    headers = {
        "Authorization": "Bearer "
        + create_access_token(staff.username, projects[0].id, staff.role)
    }
    result = await request(
        app,
        "GET",
        "/channels/info",
        params={"channel_id": str(visitors[0].id) + "-vtr", "channel_type": 251},
        headers=headers,
    )
    assert result.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "channel_id,channel_type", [("bad-vtr", 251), ("bad-staff", 1), ("bad", 1)]
)
async def test_staff_malformed_channel_is_a_controlled_error(
    profile_api, channel_id, channel_type
):
    app, _, projects, staff, _, _, _, _, _, _ = profile_api
    headers = {
        "Authorization": "Bearer "
        + create_access_token(staff[0].username, projects[0].id, staff[0].role)
    }
    result = await request(
        app,
        "GET",
        "/channels/info",
        params={"channel_id": channel_id, "channel_type": channel_type},
        headers=headers,
    )
    assert result.status_code in (400, 404)
