"""Old assist content expires without deleting frozen review evidence."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import sys
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models.assist_draft_record import AssistDraftRecord
from app.models.shadow_draft_review import ShadowDraftReview
from app.models.shadow_gate_signoff import ShadowGateSignoff
from app.models.hidden_shadow_run import HiddenShadowRun
from app.models.hidden_shadow_protocol import (
    HiddenShadowProtocol, HiddenShadowProtocolCase,
)
from app.models.hidden_shadow_gate_signoff import HiddenShadowGateSignoff
from app.models.shadow_review_batch import (
    ShadowReviewBatch, ShadowReviewBatchItem,
)
from app.models.shadow_review_protocol import (
    ShadowReviewProtocol, ShadowReviewProtocolCandidate,
)
from app.services.assist_draft_record import response_from_record
from app.services.assist_draft_retention import redact_expired_assist_drafts
from scripts.preview_assist_draft_retention import missing_retention_tables
from scripts import preview_assist_draft_retention as retention_preview


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for model in (
        AssistDraftRecord, ShadowDraftReview, ShadowReviewBatch,
        ShadowReviewBatchItem, ShadowReviewProtocol,
        ShadowReviewProtocolCandidate, ShadowGateSignoff,
        HiddenShadowRun, HiddenShadowProtocol, HiddenShadowProtocolCase,
        HiddenShadowGateSignoff,
    ):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def add_draft(db: Session, created_at: datetime) -> AssistDraftRecord:
    draft = AssistDraftRecord(
        project_id=uuid4(), staff_id=uuid4(), visitor_id=uuid4(),
        request_id=uuid4(), source_message_id="source-1",
        request_fingerprint="f" * 64, created_at=created_at,
        response_data={
            "draft": "可放 13 英寸电脑。",
            "customer_message": "我能装电脑吗？",
            "recent_messages": [{"role": "customer", "content": "旧对话"}],
            "generation_trace": [],
        },
        source_proof={"status": "verified", "content_sha256": "a" * 64},
    )
    db.add(draft)
    db.flush()
    return draft


def test_preview_requires_review_schema(db: Session) -> None:
    assert missing_retention_tables(db) == []
    db.execute(text("DROP TABLE api_shadow_gate_signoffs"))
    assert missing_retention_tables(db) == ["api_shadow_gate_signoffs"]
    db.execute(text("DROP TABLE api_hidden_shadow_gate_signoffs"))
    assert missing_retention_tables(db) == [
        "api_shadow_gate_signoffs", "api_hidden_shadow_gate_signoffs",
    ]


def test_preview_counts_both_bodies_without_redacting(
    db: Session, monkeypatch, capsys,
) -> None:
    now = datetime.now(timezone.utc)
    draft = add_draft(db, now - timedelta(days=91))
    candidate = HiddenShadowRun(
        project_id=uuid4(), platform_id=uuid4(), visitor_id=uuid4(),
        source_message_id="preview-shadow", source_message_seq=1,
        source_verified_at=now - timedelta(days=91),
        source_content_sha256="a" * 64,
        generation_version="wecom-kf-knowledge-only-v1",
        status="ready", answer="私有候选正文",
        answer_sha256="b" * 64,
        created_at=now - timedelta(days=91),
    )
    db.add(candidate)
    db.commit()
    monkeypatch.setattr(
        retention_preview, "SessionLocal", lambda: Session(db.get_bind()),
    )
    monkeypatch.setattr(
        sys, "argv", ["preview_assist_draft_retention", "--days", "90"],
    )
    retention_preview.main()
    output = capsys.readouterr().out
    assert "Eligible drafts: 1" in output
    assert "Eligible private shadow answers: 1" in output
    db.refresh(draft)
    db.refresh(candidate)
    assert draft.response_data["draft"] == "可放 13 英寸电脑。"
    assert candidate.answer == "私有候选正文"


def test_redacts_expired_body_and_preserves_review(db: Session) -> None:
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)
    expired = add_draft(db, now - timedelta(days=91))
    recent = add_draft(db, now - timedelta(days=89))
    db.add(ShadowDraftReview(
        project_id=expired.project_id, visitor_id=expired.visitor_id,
        draft_id=expired.id, reviewer_id=expired.staff_id,
        request_id=uuid4(), request_fingerprint="r" * 64,
        verdict="acceptable", reason_code=None,
    ))
    db.commit()

    assert redact_expired_assist_drafts(
        db, now=now, days=90, dry_run=True
    ) == 1
    db.refresh(expired)
    assert expired.response_data["draft"] == "可放 13 英寸电脑。"

    assert redact_expired_assist_drafts(db, now=now, days=90) == 1
    db.refresh(expired)
    db.refresh(recent)
    assert expired.response_data["draft"] == ""
    assert expired.response_data["draft_sha256"] == sha256(
        "可放 13 英寸电脑。".encode("utf-8")
    ).hexdigest()
    assert "我能装电脑吗？" not in str(expired.response_data)
    assert "旧对话" not in str(expired.response_data)
    assert expired.source_proof["status"] == "verified"
    assert db.query(ShadowDraftReview).count() == 1
    assert recent.response_data["draft"] == "可放 13 英寸电脑。"
    with pytest.raises(HTTPException) as exc:
        response_from_record(expired)
    assert exc.value.status_code == 410
    assert redact_expired_assist_drafts(db, now=now, days=90) == 0


def test_frozen_candidate_waits_for_final_signoff(db: Session) -> None:
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)
    draft = add_draft(db, now - timedelta(days=91))
    batch = ShadowReviewBatch(
        project_id=draft.project_id, creator_id=draft.staff_id,
        request_id=uuid4(), request_fingerprint="b" * 64,
        window_start=now - timedelta(days=100),
        window_end=now - timedelta(days=90), random_seed="a" * 32,
        algorithm="sha256", population_sha256="p" * 64,
        population_count=1, requested_size=1, sample_count=1,
    )
    db.add(batch)
    db.flush()
    db.add(ShadowReviewBatchItem(
        batch_id=batch.id, position=1, draft_id=draft.id,
        visitor_id=draft.visitor_id,
    ))
    protocol = ShadowReviewProtocol(
        project_id=draft.project_id, creator_id=draft.staff_id,
        request_id=uuid4(), request_fingerprint="c" * 64,
        window_start=batch.window_start, window_end=batch.window_end,
        sample_size=1, algorithm="sha256", random_seed="a" * 32,
        population_count=1, population_sha256="p" * 64,
        manifest_sha256="m" * 64, batch_id=batch.id,
        created_at=now - timedelta(days=100),
        finalized_at=now - timedelta(days=90),
    )
    db.add(protocol)
    db.flush()
    db.add(ShadowReviewProtocolCandidate(
        protocol_id=protocol.id, draft_id=draft.id,
        visitor_id=draft.visitor_id,
    ))
    db.commit()

    assert redact_expired_assist_drafts(db, now=now, days=90) == 0
    assert draft.response_data["draft"]

    signoff = ShadowGateSignoff(
        project_id=draft.project_id, protocol_id=protocol.id,
        batch_id=batch.id, requester_id=draft.staff_id,
        request_id=uuid4(), request_fingerprint="s" * 64,
        manifest_sha256="m" * 64, review_evidence_sha256="e" * 64,
        sample_count=1, evidence_reference="review-1", live_case_count=1,
        note="reviewed", decision="accepted", decider_id=draft.staff_id,
        decision_request_id=uuid4(), decision_fingerprint="d" * 64,
        decision_note="done", decided_at=now - timedelta(days=1),
    )
    db.add(signoff)
    db.commit()

    assert redact_expired_assist_drafts(db, now=now, days=90) == 1
    db.refresh(draft)
    assert draft.response_data["draft"] == ""
    assert db.query(ShadowReviewProtocolCandidate).count() == 1
    assert db.query(ShadowReviewBatchItem).count() == 1
    assert db.query(ShadowGateSignoff).count() == 1


def test_open_protocol_window_holds_old_draft(db: Session) -> None:
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)
    draft = add_draft(db, now - timedelta(days=91))
    db.add(ShadowReviewProtocol(
        project_id=draft.project_id, creator_id=draft.staff_id,
        request_id=uuid4(), request_fingerprint="o" * 64,
        window_start=now - timedelta(days=100),
        window_end=now - timedelta(days=90),
        sample_size=1, algorithm="sha256", random_seed="a" * 32,
        created_at=now - timedelta(days=100),
    ))
    db.commit()

    assert redact_expired_assist_drafts(db, now=now, days=90) == 0
    assert draft.response_data["draft"]
