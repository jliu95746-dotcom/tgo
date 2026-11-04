"""Independent reviewers grade frozen private candidates without leakage."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from pydantic import ValidationError

from app.core.exceptions import TGOAPIException
from app.models.hidden_shadow_protocol import (
    HiddenShadowProtocol, HiddenShadowProtocolCase,
)
from app.models.hidden_shadow_review import HiddenShadowCaseReview
from app.models.hidden_shadow_adjudication import HiddenShadowAdjudication
from app.models.hidden_shadow_gate_signoff import HiddenShadowGateSignoff
from app.models.hidden_shadow_run import HiddenShadowRun
from app.models.staff_message_delivery import StaffMessageDelivery
from app.schemas.hidden_shadow_review import (
    HiddenShadowReviewRequest, HiddenShadowSourceMessage,
)
from app.services.hidden_shadow_review import HiddenShadowReviewService, _utc
from app.services.hidden_shadow_protocol import _request_fingerprint


class SourceIM:
    enabled = True

    def __init__(self, message=None, error=None):
        self.message = message
        self.error = error
        self.calls = []

    async def get_message_by_client_msg_no(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.message


FROZEN_SOURCE_TEXT = "客户询问这款包的容量。"


def verified_source(protocol, case, run):
    return HiddenShadowSourceMessage(
        protocol_id=protocol.id, candidate_id=case.candidate_id,
        source_message_id=case.source_message_id,
        content=FROZEN_SOURCE_TEXT,
        content_sha256=case.source_content_sha256,
        message_seq=run.source_message_seq,
        verified_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def frozen_case():
    engine = create_engine("sqlite://")
    for model in (
        HiddenShadowProtocol, HiddenShadowRun, StaffMessageDelivery,
        HiddenShadowProtocolCase, HiddenShadowCaseReview,
        HiddenShadowAdjudication,
        HiddenShadowGateSignoff,
    ):
        model.__table__.create(engine)
    with Session(engine) as db:
        now = datetime.now(timezone.utc)
        project_id, coordinator_id, visitor_id = (
            uuid4(), uuid4(), uuid4()
        )
        answer = "请先核实订单，不要直接退款。"
        staff_reply = "请提供订单号，我帮您核对。"
        run = HiddenShadowRun(
            project_id=project_id, platform_id=uuid4(),
            visitor_id=visitor_id, source_message_id="source-123",
            source_message_seq=123, source_verified_at=now,
            source_content_sha256=sha256(
                FROZEN_SOURCE_TEXT.encode()
            ).hexdigest(),
            generation_version="knowledge-only-v1", status="ready",
            answer=answer, answer_sha256=sha256(answer.encode()).hexdigest(),
            completed_at=now - timedelta(minutes=1), created_at=now,
        )
        delivery = StaffMessageDelivery(
            project_id=project_id, staff_id=uuid4(), visitor_id=visitor_id,
            channel_id="wecom-customer", channel_type=251,
            client_msg_no="reply-123", source_message_id="source-123",
            request_fingerprint="b" * 64,
            payload={"type": 1, "content": staff_reply},
            external_status="sent", history_status="sent", created_at=now,
        )
        window_start = now - timedelta(days=1)
        seed = "d" * 32
        protocol = HiddenShadowProtocol(
            project_id=project_id, creator_id=coordinator_id,
            request_id=uuid4(), request_fingerprint=_request_fingerprint(
                window_start, now, 1,
            ),
            window_start=window_start, window_end=now,
            sample_size=1, algorithm="sha256-seed-wecom-kf-independent-v3",
            random_seed=seed,
            seed_sha256=sha256(bytes.fromhex(seed)).hexdigest(),
            population_count=1, sample_count=1,
            population_sha256="e" * 64, manifest_sha256="f" * 64,
            finalized_at=now,
            created_at=window_start - timedelta(minutes=2),
        )
        db.add_all([run, delivery, protocol])
        db.flush()
        case = HiddenShadowProtocolCase(
            protocol_id=protocol.id, candidate_id=run.id,
            visitor_id=visitor_id, staff_delivery_id=delivery.id,
            source_message_id=run.source_message_id,
            source_content_sha256=run.source_content_sha256,
            candidate_sha256=run.answer_sha256,
            staff_reply_sha256=sha256(staff_reply.encode()).hexdigest(),
            candidate_completed_at=run.completed_at,
            staff_reply_created_at=delivery.created_at,
            selected_position=1,
        )
        db.add(case)
        db.commit()
        yield db, project_id, coordinator_id, protocol, case, run, delivery
    engine.dispose()


@pytest.mark.asyncio
async def test_source_message_requires_fresh_matching_im_proof(frozen_case):
    db, project, _coordinator, protocol, case, run, _delivery = frozen_case
    source_text = "这款包能装 14 英寸电脑吗？"
    source_hash = sha256(source_text.encode()).hexdigest()
    run.source_content_sha256 = source_hash
    case.source_content_sha256 = source_hash
    db.commit()
    visitor_channel = f"{case.visitor_id}-vtr"
    message = SimpleNamespace(
        message_id=78234, message_seq=run.source_message_seq,
        client_msg_no=run.source_message_id,
        channel_id=visitor_channel, channel_type=251,
        from_uid=visitor_channel,
        timestamp=int(datetime.now(timezone.utc).timestamp()) - 20,
        payload={"type": 1, "content": source_text},
    )
    im = SourceIM(message)
    service = HiddenShadowReviewService(db)
    source = await service.get_source_message(
        project, protocol.id, case.candidate_id, im_client=im,
    )
    assert source.content == source_text
    assert source.content_sha256 == source_hash
    assert source.message_seq == run.source_message_seq
    assert im.calls[0]["client_msg_no"] == run.source_message_id
    assert im.calls[0]["raise_on_error"] is True

    with pytest.raises(TGOAPIException) as other_project:
        await service.get_source_message(
            uuid4(), protocol.id, case.candidate_id, im_client=im,
        )
    assert other_project.value.status_code == 404
    assert len(im.calls) == 1

    message.payload["content"] = "换成别的客户问题"
    with pytest.raises(TGOAPIException) as changed:
        await service.get_source_message(
            project, protocol.id, case.candidate_id, im_client=im,
        )
    assert changed.value.status_code == 409
    message.payload["content"] = source_text
    message.from_uid = "foreign-vtr"
    with pytest.raises(TGOAPIException) as foreign:
        await service.get_source_message(
            project, protocol.id, case.candidate_id, im_client=im,
        )
    assert foreign.value.status_code == 409


@pytest.mark.asyncio
async def test_source_message_fails_closed_without_im(frozen_case):
    db, project, _coordinator, protocol, case, _run, _delivery = frozen_case
    im = SourceIM(error=RuntimeError("IM unavailable"))
    service = HiddenShadowReviewService(db)
    with pytest.raises(TGOAPIException) as unavailable:
        await service.get_source_message(
            project, protocol.id, case.candidate_id, im_client=im,
        )
    assert unavailable.value.status_code == 503
    im.enabled = False
    with pytest.raises(TGOAPIException) as disabled:
        await service.get_source_message(
            project, protocol.id, case.candidate_id, im_client=im,
        )
    assert disabled.value.status_code == 503


def test_two_independent_reviews_and_blind_summary(frozen_case):
    db, project, coordinator, protocol, case, run, _delivery = frozen_case
    service = HiddenShadowReviewService(db)
    reviewer_one, reviewer_two, reviewer_three = (
        uuid4(), uuid4(), uuid4()
    )
    detail = service.get_case(project, protocol.id, case.candidate_id)
    assert detail.answer == run.answer
    assert detail.source_message_id == "source-123"
    assert detail.staff_reply == "请提供订单号，我帮您核对。"
    assert detail.visitor_id == case.visitor_id
    with pytest.raises(TGOAPIException) as other_project:
        service.get_case(uuid4(), protocol.id, case.candidate_id)
    assert other_project.value.status_code == 404

    first_request = HiddenShadowReviewRequest(
        request_id=uuid4(), verdict="acceptable",
    )
    with pytest.raises(TGOAPIException) as coordinator_denied:
        service.submit(
            project, protocol.id, case.candidate_id,
            coordinator, first_request,
        )
    assert coordinator_denied.value.status_code == 403
    first = service.submit(
        project, protocol.id, case.candidate_id,
        reviewer_one, first_request,
    )
    assert service.get_own_review(
        project, protocol.id, case.candidate_id, reviewer_one,
    ).id == first.id
    assert service.get_own_review(
        project, protocol.id, case.candidate_id, reviewer_two,
    ) is None
    with pytest.raises(TGOAPIException) as other_tenant_review:
        service.get_own_review(
            uuid4(), protocol.id, case.candidate_id, reviewer_one,
        )
    assert other_tenant_review.value.status_code == 404
    assert service.submit(
        project, protocol.id, case.candidate_id,
        reviewer_one, first_request,
    ).id == first.id
    assert service.replay(
        project, protocol.id, case.candidate_id,
        reviewer_one, first_request,
    ).id == first.id
    with pytest.raises(TGOAPIException) as changed_request:
        service.submit(
            project, protocol.id, case.candidate_id, reviewer_one,
            first_request.model_copy(update={"verdict": "severe_error"}),
        )
    assert changed_request.value.status_code == 409
    with pytest.raises(TGOAPIException) as second_opinion:
        service.submit(
            project, protocol.id, case.candidate_id, reviewer_one,
            HiddenShadowReviewRequest(
                request_id=uuid4(), verdict="minor_issue",
                reason_code="wrong_fact",
            ),
        )
    assert second_opinion.value.status_code == 409
    partial = service.summary(project, protocol.id)
    assert partial.completed_cases == 0
    assert partial.severe_cases is None
    assert partial.disputed_cases is None

    second = service.submit(
        project, protocol.id, case.candidate_id, reviewer_two,
        HiddenShadowReviewRequest(
            request_id=uuid4(), verdict="severe_error",
            reason_code="unsafe_action", note="越权承诺退款",
        ),
    )
    assert second.evidence_sha256 == first.evidence_sha256
    result = service.summary(project, protocol.id)
    assert result.completed_cases == 1
    assert result.severe_cases == 1
    assert result.disputed_cases == 1
    assert result.review_complete is True
    with pytest.raises(TGOAPIException) as third:
        service.submit(
            project, protocol.id, case.candidate_id,
            reviewer_three, first_request.model_copy(
                update={"request_id": uuid4()}
            ),
        )
    assert third.value.status_code == 409


def test_review_persists_fresh_source_proof_and_rejects_mismatch(frozen_case):
    db, project, _coordinator, protocol, case, run, _delivery = frozen_case
    service = HiddenShadowReviewService(db)
    proof = verified_source(protocol, case, run)
    receipt = service.submit(
        project, protocol.id, case.candidate_id, uuid4(),
        HiddenShadowReviewRequest(request_id=uuid4(), verdict="acceptable"),
        source_proof=proof,
    )
    assert receipt.source_verified_at is not None
    assert _utc(receipt.source_verified_at) == proof.verified_at
    assert receipt.source_message_seq == run.source_message_seq
    assert receipt.source_content_sha256 == case.source_content_sha256
    with pytest.raises(TGOAPIException) as changed:
        service.submit(
            project, protocol.id, case.candidate_id, uuid4(),
            HiddenShadowReviewRequest(
                request_id=uuid4(), verdict="acceptable",
            ),
            source_proof=proof.model_copy(update={"message_seq": 999}),
        )
    assert changed.value.status_code == 409
    assert db.query(HiddenShadowCaseReview).count() == 1


def test_changed_evidence_or_unfrozen_case_cannot_be_reviewed(frozen_case):
    db, project, _coordinator, protocol, case, run, delivery = frozen_case
    service = HiddenShadowReviewService(db)
    run.answer = "新的答案"
    db.commit()
    with pytest.raises(TGOAPIException) as changed:
        service.get_case(project, protocol.id, case.candidate_id)
    assert changed.value.status_code == 409
    with pytest.raises(TGOAPIException):
        service.submit(
            project, protocol.id, case.candidate_id, uuid4(),
            HiddenShadowReviewRequest(
                request_id=uuid4(), verdict="acceptable",
            ),
        )
    run.answer = "请先核实订单，不要直接退款。"
    delivery.payload = {"type": 1, "content": "已修改人工回复"}
    db.commit()
    with pytest.raises(TGOAPIException):
        service.get_case(project, protocol.id, case.candidate_id)
    delivery.payload = {"type": 1, "content": "请提供订单号，我帮您核对。"}
    run.source_content_sha256 = "f" * 64
    db.commit()
    with pytest.raises(TGOAPIException) as changed_source:
        service.get_case(project, protocol.id, case.candidate_id)
    assert changed_source.value.code == "HIDDEN_REVIEW_EVIDENCE_CHANGED"
    run.source_content_sha256 = case.source_content_sha256
    protocol.finalized_at = None
    protocol.population_count = None
    protocol.sample_count = None
    protocol.population_sha256 = None
    protocol.manifest_sha256 = None
    db.commit()
    with pytest.raises(TGOAPIException) as not_ready:
        service.get_case(project, protocol.id, case.candidate_id)
    assert not_ready.value.status_code == 409


def test_second_reviewer_cannot_grade_a_changed_evidence_version(frozen_case):
    db, project, _coordinator, protocol, case, run, _delivery = frozen_case
    service = HiddenShadowReviewService(db)
    first_reviewer = uuid4()
    service.submit(
        project, protocol.id, case.candidate_id, first_reviewer,
        HiddenShadowReviewRequest(
            request_id=uuid4(), verdict="acceptable",
        ),
    )
    run.answer = "已被替换的候选"
    run.answer_sha256 = sha256(run.answer.encode()).hexdigest()
    case.candidate_sha256 = run.answer_sha256
    db.commit()
    with pytest.raises(TGOAPIException) as changed:
        service.submit(
            project, protocol.id, case.candidate_id, uuid4(),
            HiddenShadowReviewRequest(
                request_id=uuid4(), verdict="acceptable",
            ),
        )
    assert changed.value.code == "HIDDEN_REVIEW_EVIDENCE_CHANGED"


def test_summary_hides_verdicts_until_every_sample_has_two_reviews(
    frozen_case,
):
    db, project, _coordinator, protocol, case, _run, _delivery = frozen_case
    service = HiddenShadowReviewService(db)
    for verdict, reason in (
        ("acceptable", None), ("severe_error", "wrong_fact"),
    ):
        service.submit(
            project, protocol.id, case.candidate_id, uuid4(),
            HiddenShadowReviewRequest(
                request_id=uuid4(), verdict=verdict, reason_code=reason,
            ),
        )
    protocol.sample_size = 2
    protocol.population_count = 2
    protocol.sample_count = 2
    db.add(HiddenShadowProtocolCase(
        protocol_id=protocol.id, candidate_id=uuid4(),
        visitor_id=uuid4(), staff_delivery_id=uuid4(),
        source_message_id="another-source",
        source_content_sha256="1" * 64,
        candidate_sha256="2" * 64, staff_reply_sha256="3" * 64,
        candidate_completed_at=datetime.now(timezone.utc),
        staff_reply_created_at=datetime.now(timezone.utc),
        selected_position=2,
    ))
    db.commit()
    summary = service.summary(project, protocol.id)
    assert summary.selected_cases == 2
    assert summary.completed_cases == 1
    assert summary.review_complete is False
    assert summary.severe_cases is None
    assert summary.disputed_cases is None


def test_review_requires_reason_for_a_serious_finding():
    with pytest.raises(ValidationError):
        HiddenShadowReviewRequest(
            request_id=uuid4(), verdict="severe_error",
        )
    with pytest.raises(ValidationError):
        HiddenShadowReviewRequest(
            request_id=uuid4(), verdict="minor_issue",
            reason_code="other", note="  ",
        )
