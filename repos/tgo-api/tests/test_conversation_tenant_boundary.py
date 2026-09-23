"""Real tenant records must authorize a channel before forwarding to IM."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import conversations
from app.core.database import get_db
from app.core.exceptions import TGOAPIException, tgo_api_exception_handler
from app.core.security import get_current_active_user
from app.models import ChannelMemoryClearance, Project, Staff, Visitor
from app.schemas.wukongim import WuKongIMChannelMessageSyncResponse
from app.schemas.wukongim import WuKongIMConversation
from app.services.ai_client import ai_client


@pytest.mark.asyncio
async def test_expired_company_admin_can_read_own_history_only(channel_app, monkeypatch):
    from datetime import timedelta
    from app.core.config import settings
    from app.models.company_account import CompanyAccount

    app, db, companies, staff, visitors, downstream = channel_app
    CompanyAccount.__table__.create(db.get_bind())
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    staff[0].role = "admin"
    db.add(CompanyAccount(project_id=companies[0].id, status="expired", seat_limit=3,
        expires_at=datetime.now(timezone.utc) - timedelta(days=1)))
    db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        own = await request_channel(client, "history", f"{visitors[0].id}-vtr", 251)
        assert own.status_code == 200
        downstream[0].reset_mock()
        foreign = await request_channel(client, "history", f"{visitors[1].id}-vtr", 251)
        assert foreign.status_code == 404
        downstream[0].assert_not_awaited()


@pytest.mark.asyncio
async def test_staff_loses_full_history_after_transfer(channel_app, monkeypatch):
    from app.core.config import settings
    from app.models import VisitorSession
    app, db, companies, staff, visitors, downstream = channel_app
    VisitorSession.__table__.create(db.get_bind())
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    staff[0].role = "user"
    receiver = Staff(project_id=companies[0].id, username="receiver",
                     password_hash="unused", role="user")
    db.add(receiver)
    db.flush()
    session = VisitorSession(project_id=companies[0].id,
                             visitor_id=visitors[0].id, staff_id=staff[0].id)
    db.add(session)
    db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        channel = f"{visitors[0].id}-vtr"
        own = await request_channel(client, "history", channel, 251)
        assert own.status_code == 200
        downstream[0].reset_mock()
        session.staff_id = receiver.id
        db.commit()
        denied = await request_channel(client, "history", channel, 251)
        assert denied.status_code == 404
        downstream[0].assert_not_awaited()


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(_type, compiler, **kwargs):
    return "JSON"


@pytest.fixture
def channel_app(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for model in (Project, Staff, Visitor, ChannelMemoryClearance):
        model.__table__.create(engine)
    with Session(engine) as db:
        companies = [Project(name=name, api_key=name) for name in ("A", "B")]
        db.add_all(companies)
        db.flush()
        staff = [Staff(project_id=company.id, username=company.name,
                       role="admin", password_hash="unused")
                 for company in companies]
        visitors = [Visitor(project_id=company.id, platform_id=uuid4(),
                            platform_open_id=company.name)
                    for company in companies]
        db.add_all(staff + visitors)
        db.commit()
        app = FastAPI()
        app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
        app.include_router(conversations.router, prefix="/conversations")
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_active_user] = lambda: staff[0]
        history = AsyncMock(return_value=WuKongIMChannelMessageSyncResponse(
            start_message_seq=0, end_message_seq=0, more=0, messages=[],
        ))
        unread, delete = AsyncMock(), AsyncMock()
        monkeypatch.setattr(conversations.wukongim_client, "sync_channel_messages", history)
        monkeypatch.setattr(conversations.wukongim_client, "set_conversation_unread", unread)
        monkeypatch.setattr(conversations.wukongim_client, "delete_conversation", delete)

        async def unchanged(result, **kwargs):
            return result

        monkeypatch.setattr(conversations, "reconcile_reply_history", unchanged)
        yield app, db, companies, staff, visitors, (history, unread, delete)
    engine.dispose()


async def request_channel(client, action, channel_id, channel_type):
    body = {"channel_id": channel_id, "channel_type": channel_type,
            "uid": "untrusted-input", "unread": 0}
    if action == "history":
        return await client.post("/conversations/messages", json=body)
    if action == "unread":
        return await client.put("/conversations/unread", json=body)
    return await client.request("DELETE", "/conversations", json=body)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "user"])
@pytest.mark.parametrize("action", ["history", "unread", "delete"])
@pytest.mark.parametrize("kind", ["visitor", "personal_visitor", "staff", "project"])
async def test_other_company_channel_never_reaches_im(channel_app, role, action, kind):
    app, _, companies, staff, visitors, downstream = channel_app
    staff[0].role = role
    channels = {
        "visitor": (f"{visitors[1].id}-vtr", 251),
        "personal_visitor": (str(visitors[1].id), 1),
        "staff": (f"{staff[1].id}-staff", 1),
        "project": (f"{companies[1].id}-prj", 249),
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await request_channel(client, action, *channels[kind])
    assert response.status_code == 404
    for forward in downstream:
        forward.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "user"])
async def test_stale_im_conversations_cannot_expose_another_company(channel_app, monkeypatch, role):
    app, _, companies, staff, visitors, _ = channel_app
    staff[0].role = role
    own = [(f"{staff[0].id}-staff", 1), (f"{companies[0].id}-prj", 249)]
    foreign = [
        (f"{staff[1].id}-staff", 1), (f"{companies[1].id}-prj", 249),
        (str(visitors[1].id), 1), ("unknown-group", 2),
    ]
    rows = [WuKongIMConversation(
        channel_id=channel_id, channel_type=kind, unread=0, timestamp=1,
        last_msg_seq=0, last_client_msg_no="", version=1, recents=[],
    ) for channel_id, kind in own + foreign]
    sync = AsyncMock(return_value=rows)
    monkeypatch.setattr(conversations.wukongim_client, "sync_conversations", sync)
    monkeypatch.setattr(conversations, "_build_channels_for_conversations", AsyncMock(return_value=[]))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.post("/conversations/my", json={})
    assert response.status_code == 200
    assert [(row["channel_id"], row["channel_type"])
            for row in response.json()["conversations"]] == own
    assert sync.await_args.kwargs["uid"] == f"{staff[0].id}-staff"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["history", "unread", "delete"])
async def test_own_company_channel_uses_server_login_identity(channel_app, action):
    app, _, _, staff, visitors, downstream = channel_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await request_channel(client, action, f"{visitors[0].id}-vtr", 251)
    assert response.status_code == 200
    forward = downstream[("history", "unread", "delete").index(action)]
    assert forward.await_count == 1
    identity_key = "login_uid" if action == "history" else "uid"
    assert forward.await_args.kwargs[identity_key] == f"{staff[0].id}-staff"


@pytest.mark.asyncio
async def test_deleted_visitor_and_unknown_channel_are_rejected(channel_app):
    app, db, _, _, visitors, downstream = channel_app
    visitors[0].deleted_at = datetime.now(timezone.utc)
    db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await request_channel(client, "history", f"{visitors[0].id}-vtr", 251)
        assert response.status_code == 404
        assert (await request_channel(client, "history", "unowned", 2)).status_code == 400
    downstream[0].assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_channel_ownership_is_checked_in_ai_service(channel_app, monkeypatch):
    app, _, companies, _, _, downstream = channel_app
    agent_id = uuid4()
    lookup = AsyncMock(side_effect=HTTPException(404, "Agent not found"))
    monkeypatch.setattr(ai_client, "get_agent", lookup)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await request_channel(client, "history", f"{agent_id}-agent", 1)
    assert response.status_code == 404
    assert lookup.await_args.kwargs["project_id"] == str(companies[0].id)
    downstream[0].assert_not_awaited()
