"""Private candidate answers obey the same retention window as staff drafts."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.hidden_shadow_run import HiddenShadowRun
from app.models.hidden_shadow_protocol import (
    HiddenShadowProtocol, HiddenShadowProtocolCase,
)
from app.models.hidden_shadow_gate_signoff import HiddenShadowGateSignoff
from app.services.hidden_shadow_retention import (
    redact_expired_hidden_shadow_runs,
)


def test_hidden_answer_is_redacted_after_90_days_but_hash_survives():
    engine = create_engine("sqlite://")
    HiddenShadowRun.__table__.create(engine)
    HiddenShadowProtocol.__table__.create(engine)
    HiddenShadowProtocolCase.__table__.create(engine)
    HiddenShadowGateSignoff.__table__.create(engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        old = HiddenShadowRun(
            project_id=uuid4(), platform_id=uuid4(), visitor_id=uuid4(),
            source_message_id="old", source_message_seq=1,
            source_verified_at=now - timedelta(days=91),
            source_content_sha256="a" * 64, status="ready", attempt_count=1,
            generation_version="wecom-kf-knowledge-only-v1",
            answer="私有候选", answer_sha256="b" * 64,
            created_at=now - timedelta(days=91),
        )
        recent = HiddenShadowRun(
            project_id=uuid4(), platform_id=uuid4(), visitor_id=uuid4(),
            source_message_id="recent", source_message_seq=1,
            source_verified_at=now - timedelta(days=20),
            source_content_sha256="c" * 64, status="ready", attempt_count=1,
            generation_version="wecom-kf-knowledge-only-v1",
            answer="近期候选", answer_sha256="d" * 64,
            created_at=now - timedelta(days=20),
        )
        db.add_all([old, recent])
        db.commit()

        assert redact_expired_hidden_shadow_runs(
            db, now=now, dry_run=True,
        ) == 1
        assert db.scalar(select(HiddenShadowRun).where(
            HiddenShadowRun.source_message_id == "old"
        )).answer == "私有候选"
        assert redact_expired_hidden_shadow_runs(db, now=now) == 1
        assert old.answer is None
        assert old.answer_sha256 == "b" * 64
        assert old.redacted_at is not None
        assert recent.answer == "近期候选"
        assert redact_expired_hidden_shadow_runs(db, now=now) == 0
    engine.dispose()


def test_frozen_unreviewed_candidate_survives_retention_window():
    engine = create_engine("sqlite://")
    HiddenShadowRun.__table__.create(engine)
    HiddenShadowProtocol.__table__.create(engine)
    HiddenShadowProtocolCase.__table__.create(engine)
    HiddenShadowGateSignoff.__table__.create(engine)
    now = datetime.now(timezone.utc)
    project_id, visitor_id = uuid4(), uuid4()
    with Session(engine) as db:
        candidate = HiddenShadowRun(
            project_id=project_id, platform_id=uuid4(),
            visitor_id=visitor_id, source_message_id="frozen",
            source_message_seq=1,
            source_verified_at=now - timedelta(days=91),
            source_content_sha256="a" * 64,
            generation_version="wecom-kf-knowledge-only-v1",
            status="ready", attempt_count=1,
            answer="待复核候选", answer_sha256="b" * 64,
            created_at=now - timedelta(days=91),
        )
        unselected = HiddenShadowRun(
            project_id=project_id, platform_id=uuid4(),
            visitor_id=visitor_id, source_message_id="unselected",
            source_message_seq=2,
            source_verified_at=now - timedelta(days=91),
            source_content_sha256="2" * 64,
            generation_version="wecom-kf-knowledge-only-v1",
            status="ready", attempt_count=1,
            answer="未抽中候选", answer_sha256="3" * 64,
            created_at=now - timedelta(days=91),
        )
        protocol = HiddenShadowProtocol(
            project_id=project_id, creator_id=uuid4(),
            request_id=uuid4(), request_fingerprint="c" * 64,
            window_start=now - timedelta(days=92),
            window_end=now - timedelta(days=90),
            sample_size=1, algorithm="sha256-seed-wecom-kf-independent-v3",
            random_seed="d" * 32, population_count=2, sample_count=1,
            population_sha256="e" * 64, manifest_sha256="f" * 64,
            created_at=now - timedelta(days=93),
            finalized_at=now - timedelta(days=90),
        )
        db.add_all([candidate, unselected, protocol])
        db.flush()
        db.add(HiddenShadowProtocolCase(
            protocol_id=protocol.id, candidate_id=candidate.id,
            visitor_id=visitor_id, staff_delivery_id=uuid4(),
            source_message_id="frozen", source_content_sha256="a" * 64,
            candidate_sha256="b" * 64, staff_reply_sha256="1" * 64,
            candidate_completed_at=now - timedelta(days=91),
            staff_reply_created_at=now - timedelta(days=90),
            selected_position=1,
        ))
        db.add(HiddenShadowProtocolCase(
            protocol_id=protocol.id, candidate_id=unselected.id,
            visitor_id=visitor_id, staff_delivery_id=uuid4(),
            source_message_id="unselected", source_content_sha256="2" * 64,
            candidate_sha256="3" * 64, staff_reply_sha256="4" * 64,
            candidate_completed_at=now - timedelta(days=91),
            staff_reply_created_at=now - timedelta(days=90),
            selected_position=None,
        ))
        db.commit()

        assert redact_expired_hidden_shadow_runs(db, now=now) == 1
        assert candidate.answer == "待复核候选"
        assert unselected.answer is None
    engine.dispose()


@pytest.mark.parametrize("decision", ("accepted", "rejected"))
def test_frozen_answer_released_only_after_same_project_signoff_decision(
    decision,
):
    engine = create_engine("sqlite://")
    for model in (
        HiddenShadowRun, HiddenShadowProtocol, HiddenShadowProtocolCase,
        HiddenShadowGateSignoff,
    ):
        model.__table__.create(engine)
    now = datetime.now(timezone.utc)
    project_id = uuid4()
    with Session(engine) as db:
        run = HiddenShadowRun(
            project_id=project_id, platform_id=uuid4(),
            visitor_id=uuid4(), source_message_id="retention-source",
            source_message_seq=1, source_verified_at=now - timedelta(days=91),
            source_content_sha256="a" * 64,
            generation_version="wecom-kf-knowledge-only-v1",
            status="ready", answer="待签收影子候选",
            answer_sha256="b" * 64,
            created_at=now - timedelta(days=91),
        )
        protocol = HiddenShadowProtocol(
            project_id=project_id, creator_id=uuid4(),
            request_id=uuid4(), request_fingerprint="c" * 64,
            window_start=now - timedelta(days=92),
            window_end=now - timedelta(days=90),
            sample_size=1, algorithm="sha256-seed-wecom-kf-independent-v3",
            random_seed="d" * 32, population_count=1, sample_count=1,
            population_sha256="e" * 64, manifest_sha256="f" * 64,
            finalized_at=now - timedelta(days=90),
        )
        db.add_all([run, protocol])
        db.flush()
        db.add(HiddenShadowProtocolCase(
            protocol_id=protocol.id, candidate_id=run.id,
            visitor_id=run.visitor_id, staff_delivery_id=uuid4(),
            source_message_id=run.source_message_id,
            source_content_sha256=run.source_content_sha256,
            candidate_sha256=run.answer_sha256,
            staff_reply_sha256="1" * 64,
            candidate_completed_at=now - timedelta(days=91),
            staff_reply_created_at=now - timedelta(days=90),
            selected_position=1,
        ))
        db.commit()
        assert redact_expired_hidden_shadow_runs(
            db, now=now, dry_run=True,
        ) == 0

        foreign_signoff = HiddenShadowGateSignoff(
            project_id=uuid4(), protocol_id=protocol.id,
            requester_id=uuid4(), request_id=uuid4(),
            request_fingerprint="2" * 64,
            manifest_sha256="f" * 64,
            review_evidence_sha256="3" * 64,
            sample_count=1, evidence_reference="audit/foreign",
            live_case_count=1, note="其他项目的签收不应释放正文",
            decision="accepted", decider_id=uuid4(),
            decision_request_id=uuid4(), decision_fingerprint="4" * 64,
            decision_note="错误的项目", decided_at=now,
        )
        db.add(foreign_signoff)
        db.commit()
        assert redact_expired_hidden_shadow_runs(
            db, now=now, dry_run=True,
        ) == 0

        signoff = HiddenShadowGateSignoff(
            project_id=project_id, protocol_id=protocol.id,
            requester_id=uuid4(), request_id=uuid4(),
            request_fingerprint="5" * 64,
            manifest_sha256="f" * 64,
            review_evidence_sha256="6" * 64,
            sample_count=1, evidence_reference="audit/pending",
            live_case_count=1, note="等待独立管理员签收",
        )
        db.add(signoff)
        db.commit()
        assert redact_expired_hidden_shadow_runs(db, now=now) == 0
        assert run.answer == "待签收影子候选"

        signoff.decision = decision
        signoff.decider_id = uuid4()
        signoff.decision_request_id = uuid4()
        signoff.decision_fingerprint = "7" * 64
        signoff.decision_note = "已完成复核，真实渠道证据不足"
        signoff.decided_at = now
        db.commit()
        assert redact_expired_hidden_shadow_runs(
            db, now=now, dry_run=True,
        ) == 1
        assert run.answer == "待签收影子候选"
        assert redact_expired_hidden_shadow_runs(db, now=now) == 1
        assert run.answer is None
        assert run.answer_sha256 == "b" * 64
        assert run.redacted_at is not None
        assert db.get(HiddenShadowGateSignoff, signoff.id) is not None
        assert redact_expired_hidden_shadow_runs(db, now=now) == 0
    engine.dispose()
