"""A reviewed aftersales task can request one scoped human handoff."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.api.v1.endpoints import ai_workflows
from app.core.database import get_db
from app.core.exceptions import TGOAPIException
from app.core.security import get_current_active_user
from app.models import Platform, Project, Visitor, VisitorSession
from app.schemas.workflow_approval import (
    WorkflowAfterSalesHandoffSummary, WorkflowAfterSalesReviewSummary,
)
from app.services import aftersales_review_handoff as handoff


class _Query:
    def __init__(self, value):
        self.value = value

    def filter(self, *_conditions):
        return self

    def first(self):
        return self.value


class _Database:
    def __init__(self, values):
        self.values = values

    def query(self, model):
        return _Query(self.values.get(model))


def _case():
    project_id, staff_id, review_id, session_id, visitor_id, platform_id = (
        uuid4() for _ in range(6)
    )
    staff = SimpleNamespace(id=staff_id, project_id=project_id)
    review = WorkflowAfterSalesReviewSummary.model_validate({
        "id": str(review_id), "execution_id": str(uuid4()),
        "approval_id": str(uuid4()), "visitor_session_id": str(session_id),
        "category": "quality_issue", "status": "reviewed",
        "review_result": "needs_manual_service", "reviewed_by": str(staff_id),
        "review_digest": "a" * 64, "created_at": "2026-10-15T00:00:00Z",
    })
    session = SimpleNamespace(
        id=session_id, project_id=project_id, visitor_id=visitor_id,
        platform_id=platform_id, status="open", closed_at=None,
    )
    visitor = SimpleNamespace(
        id=visitor_id, project_id=project_id, platform_id=platform_id,
        deleted_at=None, access_retired_at=None,
    )
    platform = SimpleNamespace(
        id=platform_id, project_id=project_id, type="wecom_kf",
        is_active=True, deleted_at=None,
    )
    project = SimpleNamespace(id=project_id, deleted_at=None)
    database = _Database({
        VisitorSession: session, Visitor: visitor,
        Platform: platform, Project: project,
    })
    return staff, review, database


@pytest.mark.asyncio
async def test_aftersales_handoff_rejects_wrong_reviewer_and_closed_session(monkeypatch):
    staff, review, db = _case()
    fetch = AsyncMock(return_value=review)
    send = AsyncMock()
    monkeypatch.setattr(handoff.workflow_client, "get_aftersales_review", fetch)
    monkeypatch.setattr(handoff, "request_human_handoff", send)
    monkeypatch.setattr(handoff, "require_staff_channel_access", AsyncMock())
    monkeypatch.setattr(handoff, "require_customer_update", lambda *_: None)

    review.reviewed_by = uuid4()
    with pytest.raises(HTTPException) as error:
        await handoff.request_aftersales_handoff(db, staff, review.id)
    assert error.value.status_code == 409

    review.reviewed_by = staff.id
    db.values[VisitorSession].project_id = uuid4()
    with pytest.raises(HTTPException) as error:
        await handoff.request_aftersales_handoff(db, staff, review.id)
    assert error.value.status_code == 409

    db.values[VisitorSession].project_id = staff.project_id
    db.values[Platform].is_active = False
    with pytest.raises(HTTPException) as error:
        await handoff.request_aftersales_handoff(db, staff, review.id)
    assert error.value.status_code == 409

    db.values[Platform].is_active = True
    db.values[VisitorSession].status = "closed"
    with pytest.raises(HTTPException) as error:
        await handoff.request_aftersales_handoff(db, staff, review.id)
    assert error.value.status_code == 409
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_aftersales_handoff_reuses_exact_source_receipt(monkeypatch):
    staff, review, db = _case()
    monkeypatch.setattr(
        handoff.workflow_client, "get_aftersales_review",
        AsyncMock(return_value=review),
    )
    monkeypatch.setattr(handoff, "require_customer_update", lambda *_: None)
    channel_access = AsyncMock()
    monkeypatch.setattr(handoff, "require_staff_channel_access", channel_access)
    source_receipt = None
    monkeypatch.setattr(
        handoff, "_saved_handoff_receipt", lambda *_: source_receipt,
    )
    send = AsyncMock(return_value={
        "request_id": str(uuid4()), "status": "active",
        "notification_status": "submitted",
    })
    monkeypatch.setattr(handoff, "request_human_handoff", send)

    first = await handoff.request_aftersales_handoff(db, staff, review.id)
    assert first.status == "active"
    assert first.notification_status == "submitted"
    assert send.await_args.kwargs["source_message_id"] == f"aftersales-review:{review.id}"
    assert send.await_args.kwargs["strict_source_receipt"] is True
    assert send.await_args.kwargs["channel"] == "wecom_kf"
    assert send.await_args.kwargs["visitor"] is db.values[Visitor]
    channel_access.assert_awaited_once()

    source_receipt = send.return_value
    db.values[VisitorSession].status = "closed"
    second = await handoff.request_aftersales_handoff(db, staff, review.id)
    assert second == first
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_aftersales_handoff_blocks_revoked_permission_and_other_open_session(
    monkeypatch,
):
    staff, review, db = _case()
    fetch = AsyncMock(return_value=review)
    send = AsyncMock()
    monkeypatch.setattr(handoff.workflow_client, "get_aftersales_review", fetch)
    monkeypatch.setattr(handoff, "request_human_handoff", send)
    monkeypatch.setattr(handoff, "require_staff_channel_access", AsyncMock())
    monkeypatch.setattr(handoff, "_saved_handoff_receipt", lambda *_: None)
    monkeypatch.setattr(handoff, "check_user_permission", lambda *_: False)
    with pytest.raises(TGOAPIException) as error:
        await handoff.request_aftersales_handoff(db, staff, review.id)
    assert error.value.status_code == 403
    fetch.assert_not_awaited()

    monkeypatch.setattr(handoff, "check_user_permission", lambda *_: True)
    db.values[VisitorSession.id] = (uuid4(),)
    with pytest.raises(HTTPException) as error:
        await handoff.request_aftersales_handoff(db, staff, review.id)
    assert error.value.status_code == 409
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_aftersales_handoff_route_uses_current_staff_and_disables_cache(monkeypatch):
    staff = SimpleNamespace(id=uuid4(), project_id=uuid4())
    review_id = uuid4()
    receipt = WorkflowAfterSalesHandoffSummary(
        request_id=uuid4(), status="waiting", notification_status="unknown",
    )
    get_receipt = AsyncMock(return_value=None)
    request = AsyncMock(return_value=receipt)
    monkeypatch.setattr(ai_workflows, "get_aftersales_handoff", get_receipt)
    monkeypatch.setattr(ai_workflows, "request_aftersales_handoff", request)
    app = FastAPI()
    app.include_router(ai_workflows.router, prefix="/v1/ai/workflows")
    db = object()
    app.dependency_overrides[get_current_active_user] = lambda: staff
    app.dependency_overrides[get_db] = lambda: db
    path = f"/v1/ai/workflows/aftersales-reviews/{review_id}/handoff"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        before = await client.get(path)
        assert before.status_code == 200 and before.json() is None
        assert before.headers["Cache-Control"] == "no-store"
        saved = await client.post(path)
        assert saved.status_code == 200
        assert saved.headers["Cache-Control"] == "no-store"
        assert saved.json()["notification_status"] == "unknown"
    get_receipt.assert_awaited_once_with(db, staff, review_id)
    request.assert_awaited_once_with(db, staff, review_id)
