"""Background turns keep the authenticated chat endpoint's current calling contract."""

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.api.v1.endpoints import chat
from app.schemas.chat import ChatCompletionRequest
from app.services import customer_turn_worker as worker
from app.services import im_access


@pytest.mark.asyncio
async def test_turn_worker_supplies_request_and_original_scoped_credentials(monkeypatch):
    turn_id, visitor_id, owner = uuid4(), uuid4(), uuid4()
    context = SimpleNamespace(visitor_id=visitor_id, staged=False, media_failure=None, version=1)
    payload = ChatCompletionRequest(api_key="fixture-only", message="查询资料", from_uid="fixture")
    visitor = SimpleNamespace(id=visitor_id)
    row = SimpleNamespace(state="sent", version=1, due_at=datetime.now(timezone.utc))

    @contextmanager
    def database():
        yield SimpleNamespace(get=lambda *_args: visitor)

    async def heartbeat(*_args):
        await asyncio.Event().wait()

    async def endpoint(req, request, db, credentials, x_saas_service_token=None):
        assert req is payload and isinstance(request, Request)
        assert db.get(None) is visitor
        assert credentials.credentials == "fixture-visitor-proof"
        assert request.state is not None
        context.staged = True

    endpoint_spy = AsyncMock(side_effect=endpoint)
    monkeypatch.setattr(worker, "begin", lambda *_args: (context, payload))
    monkeypatch.setattr(worker, "renew", heartbeat)
    monkeypatch.setattr(worker, "SessionLocal", database)
    monkeypatch.setattr(worker, "TurnStore", lambda _db: SimpleNamespace(get=lambda *_args, **_kw: row))
    monkeypatch.setattr(worker, "scheduler", SimpleNamespace(release=AsyncMock(), remove_if_due=AsyncMock()))
    monkeypatch.setattr(im_access, "issue_visitor_im_token", lambda *_args: "fixture-visitor-proof")
    monkeypatch.setattr(chat, "chat_completion", endpoint_spy)

    await worker.execute(turn_id, owner)

    assert context.staged is True
    endpoint_spy.assert_awaited_once()
