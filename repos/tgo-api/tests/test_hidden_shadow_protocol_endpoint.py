"""Independent G4 registration is admin-only and keeps its seed secret."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response

from app.core.exceptions import TGOAPIException
from app.api.v1.endpoints import hidden_shadow_protocol as endpoint
from app.main import app
from app.schemas.hidden_shadow_protocol import (
    HiddenShadowProtocolCreateRequest,
)
from app.schemas.hidden_shadow_review import HiddenShadowReviewRequest
from app.schemas.hidden_shadow_adjudication import (
    HiddenShadowAdjudicationRequest,
)
from app.schemas.shadow_gate_signoff import (
    ShadowGateDecisionRequest, ShadowGateSignoffRequest,
)


@pytest.mark.asyncio
async def test_hidden_protocol_routes_require_admin():
    prefix = "/v1/message-analysis/staff/hidden-shadow-protocols"
    paths = app.openapi()["paths"]
    assert "post" in paths[prefix]
    assert "get" in paths[prefix]
    assert "get" in paths[f"{prefix}/{{protocol_id}}"]
    assert "post" in paths[f"{prefix}/{{protocol_id}}/finalize"]
    assert "post" in paths[f"{prefix}/{{protocol_id}}/cancel"]
    assert "get" in paths[f"{prefix}/{{protocol_id}}/cases"]
    assert "get" in paths[f"{prefix}/{{protocol_id}}/integrity"]
    assert "get" in paths[f"{prefix}/{{protocol_id}}/review-summary"]
    assert "get" in paths[f"{prefix}/{{protocol_id}}/gate-readiness"]
    assert "get" in paths[f"{prefix}/{{protocol_id}}/signoff"]
    assert "post" in paths[f"{prefix}/{{protocol_id}}/signoff"]
    assert "post" in paths[f"{prefix}/{{protocol_id}}/signoff/decision"]
    assert "get" in paths[f"{prefix}/{{protocol_id}}/cases/{{candidate_id}}"]
    assert "get" in paths[
        f"{prefix}/{{protocol_id}}/cases/{{candidate_id}}/source-message"
    ]
    assert "get" in paths[
        f"{prefix}/{{protocol_id}}/cases/{{candidate_id}}/my-review"
    ]
    assert (
        "post" in paths[
            f"{prefix}/{{protocol_id}}/cases/{{candidate_id}}/reviews"
        ]
    )
    assert (
        "get" in paths[
            f"{prefix}/{{protocol_id}}/cases/{{candidate_id}}/reviews"
        ]
    )
    assert (
        "post" in paths[
            f"{prefix}/{{protocol_id}}/cases/{{candidate_id}}/adjudication"
        ]
    )
    for route in endpoint.router.routes:
        admin_check = next(
            dependency.call for dependency in route.dependant.dependencies
            if dependency.name == "current_user"
        )
        with pytest.raises(HTTPException) as denied:
            await admin_check(SimpleNamespace(role="agent", username="staff"))
        assert denied.value.status_code == 403


def test_register_uses_authenticated_project_and_hides_seed(monkeypatch):
    now = datetime.now(timezone.utc)
    request = HiddenShadowProtocolCreateRequest(
        request_id=uuid4(),
        window_start=now + timedelta(minutes=2),
        window_end=now + timedelta(days=1),
        sample_size=2,
    )
    admin = SimpleNamespace(id=uuid4(), project_id=uuid4(), role="admin")
    row = SimpleNamespace(
        id=uuid4(), request_id=request.request_id,
        created_at=now, window_start=request.window_start,
        window_end=request.window_end, sample_size=2,
        algorithm="sha256-seed-wecom-kf-independent-v3",
        random_seed="1a" * 16, seed_sha256="c" * 64,
        finalized_at=None, cancelled_at=None, cancelled_by=None,
        population_count=None, sample_count=None,
        population_sha256=None, manifest_sha256=None,
    )
    service = MagicMock()
    service.register.return_value = row
    monkeypatch.setattr(
        endpoint, "HiddenShadowProtocolService", lambda _db: service,
    )
    response = Response()
    result = endpoint.register_hidden_shadow_protocol(
        request=request, response=response,
        current_user=admin, db=MagicMock(),
    )
    service.register.assert_called_once_with(
        project_id=admin.project_id, creator_id=admin.id,
        request_id=request.request_id,
        window_start=request.window_start,
        window_end=request.window_end,
        sample_size=2,
    )
    assert result.random_seed is None
    assert result.seed_sha256 == "c" * 64
    assert response.headers["Cache-Control"] == "no-store"

    row.finalized_at = now + timedelta(days=1)
    row.population_count = 0
    row.sample_count = 0
    row.population_sha256 = "a" * 64
    row.manifest_sha256 = "b" * 64
    assert endpoint._response(row).random_seed == row.random_seed


def test_list_uses_authenticated_project_and_hides_unreleased_seed(
    monkeypatch,
):
    now = datetime.now(timezone.utc)
    admin = SimpleNamespace(id=uuid4(), project_id=uuid4(), role="admin")
    row = SimpleNamespace(
        id=uuid4(), request_id=uuid4(), created_at=now,
        window_start=now + timedelta(minutes=2),
        window_end=now + timedelta(days=1), sample_size=2,
        algorithm="sha256-seed-wecom-kf-independent-v3",
        random_seed="1a" * 16, seed_sha256=None,
        finalized_at=None, cancelled_at=None, cancelled_by=None,
        population_count=None, sample_count=None,
        population_sha256=None, manifest_sha256=None,
    )
    service = MagicMock()
    service.list.return_value = ([row], False)
    monkeypatch.setattr(
        endpoint, "HiddenShadowProtocolService", lambda _db: service,
    )
    response = Response()
    result = endpoint.list_hidden_shadow_protocols(
        response=response, limit=20, current_user=admin, db=MagicMock(),
    )
    service.list.assert_called_once_with(admin.project_id, limit=20)
    assert len(result.items) == 1
    assert result.items[0].random_seed is None
    assert result.items[0].seed_sha256 is None
    assert not result.has_more
    assert response.headers["Cache-Control"] == "no-store"


def test_cancel_uses_authenticated_project_and_actor(monkeypatch):
    now = datetime.now(timezone.utc)
    protocol_id = uuid4()
    admin = SimpleNamespace(id=uuid4(), project_id=uuid4(), role="admin")
    row = SimpleNamespace(
        id=protocol_id, request_id=uuid4(), created_at=now,
        window_start=now + timedelta(minutes=2),
        window_end=now + timedelta(days=1), sample_size=2,
        algorithm="sha256-seed-wecom-kf-independent-v3",
        random_seed="1a" * 16, seed_sha256=None,
        finalized_at=None, cancelled_at=now, cancelled_by=admin.id,
        population_count=None, sample_count=None,
        population_sha256=None, manifest_sha256=None,
    )
    service = MagicMock()
    service.cancel.return_value = row
    monkeypatch.setattr(
        endpoint, "HiddenShadowProtocolService", lambda _db: service,
    )
    response = Response()
    result = endpoint.cancel_hidden_shadow_protocol(
        protocol_id=protocol_id, response=response,
        current_user=admin, db=MagicMock(),
    )
    service.cancel.assert_called_once_with(
        admin.project_id, protocol_id, actor_id=admin.id,
    )
    assert result.status == "cancelled"
    assert result.random_seed is None
    assert result.cancelled_by == admin.id
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_review_submission_uses_authenticated_tenant_and_reviewer(
    monkeypatch,
):
    project_id, reviewer_id, protocol_id, candidate_id = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    actor = SimpleNamespace(id=reviewer_id, project_id=project_id)
    request = HiddenShadowReviewRequest(
        request_id=uuid4(), verdict="acceptable",
    )
    service = MagicMock()
    source_proof = SimpleNamespace(content="customer message")
    service.get_source_message = AsyncMock(return_value=source_proof)
    service.replay.return_value = None
    service.submit.return_value = SimpleNamespace(id=uuid4())
    monkeypatch.setattr(
        endpoint, "HiddenShadowReviewService", lambda _db: service,
    )
    response = Response()
    result = await endpoint.submit_hidden_shadow_review(
        protocol_id=protocol_id, candidate_id=candidate_id,
        request=request, response=response,
        current_user=actor, db=MagicMock(),
    )
    assert result == service.submit.return_value
    service.replay.assert_called_once_with(
        project_id, protocol_id, candidate_id, reviewer_id, request,
    )
    service.get_source_message.assert_awaited_once_with(
        project_id, protocol_id, candidate_id,
        im_client=endpoint.wukongim_client,
    )
    service.submit.assert_called_once_with(
        project_id, protocol_id, candidate_id, reviewer_id, request,
        source_proof=source_proof,
    )
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_review_submission_stops_when_source_cannot_be_verified(
    monkeypatch,
):
    service = MagicMock()
    service.replay.return_value = None
    service.get_source_message = AsyncMock(side_effect=TGOAPIException(
        "原消息不可用", "HIDDEN_REVIEW_SOURCE_UNAVAILABLE", status_code=503,
    ))
    monkeypatch.setattr(
        endpoint, "HiddenShadowReviewService", lambda _db: service,
    )
    with pytest.raises(TGOAPIException) as failed:
        await endpoint.submit_hidden_shadow_review(
            protocol_id=uuid4(), candidate_id=uuid4(),
            request=HiddenShadowReviewRequest(
                request_id=uuid4(), verdict="acceptable",
            ),
            response=Response(),
            current_user=SimpleNamespace(id=uuid4(), project_id=uuid4()),
            db=MagicMock(),
        )
    assert failed.value.status_code == 503
    service.submit.assert_not_called()


@pytest.mark.asyncio
async def test_review_retry_replays_saved_receipt_without_im(monkeypatch):
    service = MagicMock()
    saved = SimpleNamespace(id=uuid4())
    service.replay.return_value = saved
    monkeypatch.setattr(
        endpoint, "HiddenShadowReviewService", lambda _db: service,
    )
    result = await endpoint.submit_hidden_shadow_review(
        protocol_id=uuid4(), candidate_id=uuid4(),
        request=HiddenShadowReviewRequest(
            request_id=uuid4(), verdict="acceptable",
        ),
        response=Response(),
        current_user=SimpleNamespace(id=uuid4(), project_id=uuid4()),
        db=MagicMock(),
    )
    assert result == saved
    service.get_source_message.assert_not_called()
    service.submit.assert_not_called()


def test_my_review_uses_authenticated_tenant_and_reviewer(monkeypatch):
    project_id, reviewer_id, protocol_id, candidate_id = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    actor = SimpleNamespace(id=reviewer_id, project_id=project_id)
    service = MagicMock()
    service.get_own_review.return_value = None
    monkeypatch.setattr(
        endpoint, "HiddenShadowReviewService", lambda _db: service,
    )
    response = Response()
    result = endpoint.get_hidden_shadow_own_review(
        protocol_id=protocol_id, candidate_id=candidate_id,
        response=response, current_user=actor, db=MagicMock(),
    )
    assert result is None
    service.get_own_review.assert_called_once_with(
        project_id, protocol_id, candidate_id, reviewer_id,
    )
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_source_message_uses_authenticated_project(monkeypatch):
    project_id, protocol_id, candidate_id = uuid4(), uuid4(), uuid4()
    actor = SimpleNamespace(id=uuid4(), project_id=project_id)
    service = MagicMock()
    service.get_source_message = AsyncMock(return_value=SimpleNamespace(
        content="customer message",
    ))
    monkeypatch.setattr(
        endpoint, "HiddenShadowReviewService", lambda _db: service,
    )
    response = Response()
    result = await endpoint.get_hidden_shadow_source_message(
        protocol_id=protocol_id, candidate_id=candidate_id,
        response=response, current_user=actor, db=MagicMock(),
    )
    assert result.content == "customer message"
    service.get_source_message.assert_awaited_once_with(
        project_id, protocol_id, candidate_id,
        im_client=endpoint.wukongim_client,
    )
    assert response.headers["Cache-Control"] == "no-store"


def test_adjudication_submission_uses_authenticated_tenant(monkeypatch):
    project_id, actor_id, protocol_id, candidate_id = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    actor = SimpleNamespace(id=actor_id, project_id=project_id)
    request = HiddenShadowAdjudicationRequest(
        request_id=uuid4(), expected_review_sha256="a" * 64,
        verdict="acceptable", note="已核对两份复核和来源。",
    )
    service = MagicMock()
    service.submit.return_value = SimpleNamespace(id=uuid4())
    monkeypatch.setattr(
        endpoint, "HiddenShadowAdjudicationService",
        lambda _db: service,
    )
    response = Response()
    result = endpoint.submit_hidden_shadow_adjudication(
        protocol_id=protocol_id, candidate_id=candidate_id,
        request=request, response=response,
        current_user=actor, db=MagicMock(),
    )
    assert result == service.submit.return_value
    service.submit.assert_called_once_with(
        project_id, protocol_id, candidate_id, actor_id, request,
    )
    assert response.headers["Cache-Control"] == "no-store"


def test_gate_readiness_uses_authenticated_tenant(monkeypatch):
    project_id, protocol_id = uuid4(), uuid4()
    actor = SimpleNamespace(id=uuid4(), project_id=project_id)
    service = MagicMock()
    monkeypatch.setattr(
        endpoint, "HiddenShadowGateAssessmentService",
        lambda _db: service,
    )
    response = Response()
    result = endpoint.get_hidden_shadow_gate_readiness(
        protocol_id=protocol_id, response=response,
        current_user=actor, db=MagicMock(),
    )
    assert result == service.assess.return_value
    service.assess.assert_called_once_with(project_id, protocol_id)
    assert response.headers["Cache-Control"] == "no-store"


def test_signoff_routes_use_authenticated_tenant_and_actor(monkeypatch):
    project_id, actor_id, protocol_id = uuid4(), uuid4(), uuid4()
    actor = SimpleNamespace(id=actor_id, project_id=project_id)
    service = MagicMock()
    monkeypatch.setattr(
        endpoint, "HiddenShadowGateSignoffService",
        lambda _db: service,
    )
    request = ShadowGateSignoffRequest(
        request_id=uuid4(), evidence_reference="wecom-kf-audit/01",
        live_case_count=1,
        note="已核对企业微信原消息和真实发送回执。",
    )
    decision = ShadowGateDecisionRequest(
        request_id=uuid4(), decision="rejected",
        live_evidence_verified=False, note="真实证据不完整。",
    )
    response = Response()
    endpoint.get_hidden_shadow_gate_signoff(
        protocol_id=protocol_id, response=response,
        current_user=actor, db=MagicMock(),
    )
    service.get.assert_called_once_with(project_id, protocol_id)
    endpoint.request_hidden_shadow_gate_signoff(
        protocol_id=protocol_id, request=request, response=response,
        current_user=actor, db=MagicMock(),
    )
    service.request.assert_called_once_with(
        project_id, protocol_id, actor_id, request,
    )
    endpoint.decide_hidden_shadow_gate_signoff(
        protocol_id=protocol_id, request=decision, response=response,
        current_user=actor, db=MagicMock(),
    )
    service.decide.assert_called_once_with(
        project_id, protocol_id, actor_id, decision,
    )
    assert response.headers["Cache-Control"] == "no-store"
