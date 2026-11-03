"""Official G4 protocol API is administrator-only and project-scoped."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response

from app.api.v1.endpoints import message_analysis
from app.core.exceptions import TGOAPIException
from app.main import app
from app.schemas.shadow_sample import ShadowProtocolCreateRequest
from app.schemas.shadow_gate_signoff import (
    ShadowGateDecisionRequest, ShadowGateSignoffRequest,
)


def test_protocol_routes_are_registered() -> None:
    path = "/v1/message-analysis/staff/shadow-review-protocol"
    assert {"get", "post"}.issubset(app.openapi()["paths"][path])
    assert "post" in app.openapi()["paths"][f"{path}/finalize"]
    assert "get" in app.openapi()["paths"][f"{path}s"]
    assert "get" in app.openapi()["paths"][f"{path}s/{{protocol_id}}/candidates"]
    assert "get" in app.openapi()["paths"][f"{path}s/{{protocol_id}}/integrity"]
    assert "get" in app.openapi()["paths"][f"{path}s/{{protocol_id}}/readiness"]
    assert {"get", "post"}.issubset(
        app.openapi()["paths"][f"{path}s/{{protocol_id}}/signoff"]
    )
    assert "post" in app.openapi()["paths"][
        f"{path}s/{{protocol_id}}/signoff/decision"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/staff/shadow-review-protocol", "GET"),
        ("/staff/shadow-review-protocol", "POST"),
        ("/staff/shadow-review-protocol/finalize", "POST"),
        ("/staff/shadow-review-protocols", "GET"),
        ("/staff/shadow-review-protocols/{protocol_id}/candidates", "GET"),
        ("/staff/shadow-review-protocols/{protocol_id}/integrity", "GET"),
        ("/staff/shadow-review-protocols/{protocol_id}/readiness", "GET"),
        ("/staff/shadow-review-protocols/{protocol_id}/signoff", "GET"),
        ("/staff/shadow-review-protocols/{protocol_id}/signoff", "POST"),
        ("/staff/shadow-review-protocols/{protocol_id}/signoff/decision", "POST"),
    ],
)
async def test_protocol_routes_require_admin(path: str, method: str) -> None:
    route = next(
        item
        for item in message_analysis.router.routes
        if item.path == path and method in item.methods
    )
    admin_check = next(
        dependency.call
        for dependency in route.dependant.dependencies
        if dependency.name == "current_user"
    )
    with pytest.raises(HTTPException) as denied:
        await admin_check(SimpleNamespace(role="agent", username="staff"))
    assert denied.value.status_code == 403


def test_protocol_routes_disable_cache_and_use_admin_project(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    request = ShadowProtocolCreateRequest(
        request_id=uuid4(),
        window_start=now + timedelta(days=1),
        window_end=now + timedelta(days=8),
        sample_size=200,
    )
    admin = SimpleNamespace(id=uuid4(), project_id=uuid4(), role="admin")
    service = MagicMock()
    monkeypatch.setattr(
        message_analysis, "ShadowReviewProtocolService", lambda _db: service
    )
    response = Response()
    message_analysis.register_staff_shadow_review_protocol(
        request=request,
        response=response,
        current_user=admin,
        db=MagicMock(),  # type: ignore[arg-type]
    )
    service.register.assert_called_once_with(admin.project_id, admin.id, request)
    assert response.headers["Cache-Control"] == "no-store"

    protocol_id, after_draft_id = uuid4(), uuid4()
    response = Response()
    message_analysis.verify_staff_shadow_review_protocol_integrity(
        protocol_id=protocol_id,
        response=response,
        current_user=admin,
        db=MagicMock(),  # type: ignore[arg-type]
    )
    service.verify_integrity.assert_called_once_with(admin.project_id, protocol_id)
    assert response.headers["Cache-Control"] == "no-store"

    response = Response()
    message_analysis.get_staff_shadow_review_protocol(
        response=response,
        current_user=admin,
        db=MagicMock(),  # type: ignore[arg-type]
    )
    service.get.assert_called_once_with(admin.project_id)
    assert response.headers["Cache-Control"] == "no-store"

    response = Response()
    message_analysis.finalize_staff_shadow_review_protocol(
        response=response,
        current_user=admin,
        db=MagicMock(),  # type: ignore[arg-type]
    )
    service.finalize.assert_called_once_with(admin.project_id)
    assert response.headers["Cache-Control"] == "no-store"

    response = Response()
    message_analysis.list_staff_shadow_review_protocols(
        response=response,
        limit=20,
        current_user=admin,
        db=MagicMock(),  # type: ignore[arg-type]
    )
    service.list.assert_called_once_with(admin.project_id, limit=20)
    assert response.headers["Cache-Control"] == "no-store"

    response = Response()
    message_analysis.list_staff_shadow_review_protocol_candidates(
        protocol_id=protocol_id,
        response=response,
        limit=100,
        after_draft_id=after_draft_id,
        current_user=admin,
        db=MagicMock(),  # type: ignore[arg-type]
    )
    service.list_candidates.assert_called_once_with(
        admin.project_id, protocol_id, limit=100, after_draft_id=after_draft_id
    )
    assert response.headers["Cache-Control"] == "no-store"


def test_shadow_gate_readiness_uses_admin_scope_and_blind_access(monkeypatch) -> None:
    admin = SimpleNamespace(id=uuid4(), project_id=uuid4(), role="admin")
    protocol_id = uuid4()
    gate = MagicMock()
    blind = MagicMock()
    monkeypatch.setattr(message_analysis, "ShadowGateAssessmentService", lambda _db: gate)
    monkeypatch.setattr(message_analysis, "ShadowBlindReviewAccess", lambda _db: blind)
    response = Response()

    message_analysis.get_staff_shadow_gate_readiness(
        protocol_id=protocol_id,
        response=response,
        current_user=admin,
        db=MagicMock(),  # type: ignore[arg-type]
    )

    blind.require_result_access.assert_called_once_with(admin.project_id, admin.id)
    gate.assess.assert_called_once_with(admin.project_id, protocol_id)
    assert response.headers["Cache-Control"] == "no-store"


def test_shadow_gate_readiness_does_not_reveal_blind_results(monkeypatch) -> None:
    admin = SimpleNamespace(id=uuid4(), project_id=uuid4(), role="admin")
    gate = MagicMock()
    blind = MagicMock()
    blind.require_result_access.side_effect = TGOAPIException(
        "正式盲审期间不可读取结果",
        "SHADOW_BLIND_REVIEW_LOCKED",
        status_code=403,
    )
    monkeypatch.setattr(
        message_analysis, "ShadowGateAssessmentService", lambda _db: gate,
    )
    monkeypatch.setattr(
        message_analysis, "ShadowBlindReviewAccess", lambda _db: blind,
    )

    with pytest.raises(TGOAPIException) as denied:
        message_analysis.get_staff_shadow_gate_readiness(
            protocol_id=uuid4(),
            response=Response(),
            current_user=admin,
            db=MagicMock(),  # type: ignore[arg-type]
        )

    assert denied.value.status_code == 403
    gate.assess.assert_not_called()


def test_shadow_gate_signoff_routes_keep_admin_scope_and_blind_gate(monkeypatch) -> None:
    admin = SimpleNamespace(id=uuid4(), project_id=uuid4(), role="admin")
    protocol_id = uuid4()
    service = MagicMock()
    blind = MagicMock()
    monkeypatch.setattr(message_analysis, "ShadowGateSignoffService", lambda _db: service)
    monkeypatch.setattr(message_analysis, "ShadowBlindReviewAccess", lambda _db: blind)
    db = MagicMock()
    response = Response()
    request = ShadowGateSignoffRequest(
        request_id=uuid4(), evidence_reference="WECOM-REHEARSAL-001",
        live_case_count=2, note="已核对真实渠道的样本记录。",
    )
    decision = ShadowGateDecisionRequest(
        request_id=uuid4(), decision="rejected", note="证据尚不完整",
        live_evidence_verified=False,
    )

    message_analysis.get_staff_shadow_gate_signoff(
        protocol_id, response, admin, db,
    )
    message_analysis.request_staff_shadow_gate_signoff(
        protocol_id, request, response, admin, db,
    )
    message_analysis.decide_staff_shadow_gate_signoff(
        protocol_id, decision, response, admin, db,
    )

    assert blind.require_result_access.call_count == 3
    blind.require_result_access.assert_called_with(admin.project_id, admin.id)
    service.get.assert_called_once_with(admin.project_id, protocol_id)
    service.request.assert_called_once_with(admin.project_id, protocol_id, admin.id, request)
    service.decide.assert_called_once_with(admin.project_id, protocol_id, admin.id, decision)
    assert response.headers["Cache-Control"] == "no-store"
