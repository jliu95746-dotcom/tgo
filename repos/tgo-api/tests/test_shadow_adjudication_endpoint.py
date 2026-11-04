"""Adjudication is an admin-only, project-scoped, uncached command."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError

from app.api.v1.endpoints import message_analysis
from app.main import app
from app.schemas.shadow_adjudication import ShadowAdjudicationRequest


@pytest.mark.asyncio
async def test_adjudication_route_is_registered_and_requires_admin() -> None:
    path = "/v1/message-analysis/staff/shadow-adjudications"
    assert "post" in app.openapi()["paths"][path]
    route = next(
        item
        for item in message_analysis.router.routes
        if item.path == "/staff/shadow-adjudications" and "POST" in item.methods
    )
    admin_check = next(
        dependency.call
        for dependency in route.dependant.dependencies
        if dependency.name == "current_user"
    )
    with pytest.raises(HTTPException) as denied:
        await admin_check(SimpleNamespace(role="agent", username="staff"))
    assert denied.value.status_code == 403

    assert "get" in app.openapi()["paths"][path]
    read_route = next(
        item
        for item in message_analysis.router.routes
        if item.path == "/staff/shadow-adjudications" and "GET" in item.methods
    )
    read_admin_check = next(
        dependency.call
        for dependency in read_route.dependant.dependencies
        if dependency.name == "current_user"
    )
    with pytest.raises(HTTPException) as read_denied:
        await read_admin_check(SimpleNamespace(role="agent", username="staff"))
    assert read_denied.value.status_code == 403


def test_adjudication_command_requires_reason_and_rationale() -> None:
    base = {
        "request_id": uuid4(),
        "batch_id": uuid4(),
        "draft_id": uuid4(),
        "visitor_id": uuid4(),
        "expected_review_sha256": "a" * 64,
        "verdict": "minor_issue",
        "note": "  ",
    }
    with pytest.raises(ValidationError):
        ShadowAdjudicationRequest.model_validate(base)
    with pytest.raises(ValidationError):
        ShadowAdjudicationRequest.model_validate({**base, "note": "核对后有误"})


def test_adjudication_route_checks_visitor_and_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = SimpleNamespace(id=uuid4(), project_id=uuid4(), role="admin")
    request = ShadowAdjudicationRequest(
        request_id=uuid4(),
        batch_id=uuid4(),
        draft_id=uuid4(),
        visitor_id=uuid4(),
        expected_review_sha256="a" * 64,
        verdict="acceptable",
        note="已逐项核对来源和人工回复。",
    )
    scope = MagicMock()
    service = MagicMock()
    receipt = SimpleNamespace(id=uuid4())
    service.submit.return_value = receipt
    monkeypatch.setattr(
        message_analysis, "ShadowAdjudicationService", lambda _db: service
    )
    monkeypatch.setattr(
        "app.services.staff_conversation_scope.require_owned_visitor", scope
    )
    database = MagicMock()
    response = Response()
    assert (
        message_analysis.submit_staff_shadow_adjudication(
            request=request, response=response, current_user=admin, db=database
        )
        is receipt
    )
    assert response.headers["Cache-Control"] == "no-store"
    scope.assert_called_once_with(database, admin, request.visitor_id)
    service.submit.assert_called_once_with(admin.project_id, admin.id, request)


def test_adjudication_history_route_checks_scope_and_disables_caching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = SimpleNamespace(id=uuid4(), project_id=uuid4(), role="admin")
    batch_id, draft_id, visitor_id = uuid4(), uuid4(), uuid4()
    scope, service, database = MagicMock(), MagicMock(), MagicMock()
    receipt_page = SimpleNamespace(items=(), has_more=False)
    service.list_history.return_value = receipt_page
    monkeypatch.setattr(
        message_analysis, "ShadowAdjudicationService", lambda _db: service
    )
    monkeypatch.setattr(
        "app.services.staff_conversation_scope.require_owned_visitor", scope
    )
    response = Response()

    result = message_analysis.list_staff_shadow_adjudications(
        batch_id=batch_id,
        draft_id=draft_id,
        visitor_id=visitor_id,
        limit=10,
        before_id=None,
        response=response,
        current_user=admin,
        db=database,
    )
    assert result is receipt_page
    assert response.headers["Cache-Control"] == "no-store"
    scope.assert_called_once_with(database, admin, visitor_id)
    service.list_history.assert_called_once_with(
        admin.project_id,
        batch_id,
        draft_id,
        visitor_id,
        limit=10,
        before_id=None,
    )
