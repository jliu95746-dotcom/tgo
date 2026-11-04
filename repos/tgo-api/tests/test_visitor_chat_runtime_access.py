"""Original visitor ownership survives awaits, producer queues and response writes."""

import asyncio
from contextlib import aclosing
from datetime import timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.core.exceptions import TGOAPIException
from app.schemas.ai_runs import ReplyRun
from app.services import ai_reply_control as control
from app.services import visitor_chat_access as access_service
from app.services.im_access import issue_visitor_im_token
from app.services.run_registry import InMemoryRunRegistry
from app.services.visitor_data_access import require_visitor_data_access
from tests.test_im_access import records as records

pytestmark = pytest.mark.asyncio


@pytest.fixture
def owned(records, monkeypatch):
    db, _, _, platforms, visitors = records
    token = issue_visitor_im_token(visitors[0], timedelta(hours=1))
    original = require_visitor_data_access(
        db, platforms[0], HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    )
    monkeypatch.setattr(access_service, "SessionLocal", lambda: Session(db.get_bind()))
    registry = InMemoryRunRegistry()
    monkeypatch.setattr(control, "run_registry", registry)
    monkeypatch.setattr(control, "POLL_SECONDS", 0.005)
    monkeypatch.setattr(control, "PHASE_WAIT_SECONDS", 0.02)
    identity = ReplyRun(
        project_id=str(visitors[0].project_id), client_msg_no=uuid4().hex,
        channel_id=f"{visitors[0].id}-vtr", channel_type=251,
    )

    def revoke():
        visitors[0].access_revision += 1
        db.commit()

    return original, registry, identity, revoke


async def test_revoked_before_await_never_starts_the_operation(owned):
    access, _, _, revoke = owned
    operation = AsyncMock()
    revoke()
    with pytest.raises(TGOAPIException):
        await access_service.guarded_chat_call(operation, access)
    operation.assert_not_awaited()


async def test_revoked_during_await_does_not_release_the_result(owned):
    access, _, _, revoke = owned

    async def operation():
        revoke()
        return "private-result"

    with pytest.raises(TGOAPIException):
        await access_service.guarded_chat_call(operation, access)


async def test_revoked_during_registry_start_never_creates_a_producer(owned, monkeypatch):
    access, registry, identity, revoke = owned
    original_start = registry.start

    async def start(item):
        result = await original_start(item)
        revoke()
        return result

    monkeypatch.setattr(registry, "start", start)
    source = Mock()
    with access_service.scoped_chat_access(access), pytest.raises(TGOAPIException):
        async for _ in control.controlled_reply(identity, source, AsyncMock(), AsyncMock()):
            pytest.fail("revoked visitor received an event")
    source.assert_not_called()


async def test_stream_read_rechecks_before_exposing_model_output(owned):
    access, _, _, revoke = owned
    closed = asyncio.Event()

    async def source(_phase):
        try:
            revoke()
            yield "event", {"event_type": "agent_content_chunk", "data": {"content": "private"}}
        finally:
            closed.set()

    with access_service.scoped_chat_access(access), pytest.raises(TGOAPIException):
        async for _ in control.tracked_ai_stream(source):
            pytest.fail("revoked model output escaped")
    assert closed.is_set()


async def test_queued_reply_rechecks_before_the_next_consumer_yield(owned):
    access, _, identity, revoke = owned
    published = AsyncMock()
    closed = asyncio.Event()

    async def source():
        try:
            yield {"event_type": "agent_content_chunk", "data": {"content": "first"}}
            yield {"event_type": "workflow_completed", "data": {"success": True, "final_content": "private"}}
        finally:
            closed.set()

    with access_service.scoped_chat_access(access):
        async with aclosing(control.controlled_reply(identity, source, published, AsyncMock())) as stream:
            first = await anext(stream)
            assert first["event_type"] == "agent_content_chunk"
            revoke()
            with pytest.raises(TGOAPIException):
                await anext(stream)
    assert closed.is_set()


async def test_revoked_at_publication_boundary_never_releases_reply(owned, monkeypatch):
    access, registry, identity, revoke = owned
    original = registry.begin_publication

    async def begin(item):
        result = await original(item)
        revoke()
        return result

    monkeypatch.setattr(registry, "begin_publication", begin)
    released = []

    async def source():
        await control.begin_reply_publication()
        released.append("private")
        yield {"event_type": "workflow_completed", "data": {"success": True}}

    with access_service.scoped_chat_access(access), pytest.raises(TGOAPIException):
        async for _ in control.controlled_reply(identity, source, AsyncMock(), AsyncMock()):
            pytest.fail("revoked reply escaped")
    assert released == []


@pytest.mark.parametrize("boundary", ["start", "body"])
async def test_response_checks_before_headers_and_each_body_chunk(owned, boundary):
    access, _, _, revoke = owned
    received = []
    if boundary == "start":
        revoke()

    async def send(message):
        received.append(message)
        if boundary == "body" and message["type"] == "http.response.start":
            revoke()

    response = access_service.AuthorizedChatResponse(Response("private-result"), access)
    with pytest.raises(TGOAPIException):
        await response({"type": "http"}, AsyncMock(), send)
    assert all(item["type"] != "http.response.body" for item in received)
    assert len(received) == int(boundary == "body")
