"""A future-registered G4 seed freezes private AI/human comparisons."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.hidden_shadow_protocol import (
    HiddenShadowProtocol, HiddenShadowProtocolCase,
)
from app.models.shadow_review_protocol import ShadowReviewProtocol
from app.services.hidden_shadow_pairing import HiddenShadowComparison
from app.services.hidden_shadow_protocol import HiddenShadowProtocolService


def test_protocol_list_is_project_scoped_and_newest_first():
    engine = create_engine("sqlite://")
    HiddenShadowProtocol.__table__.create(engine)
    project_id, other_project_id = uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        for index, tenant in enumerate((
            project_id, other_project_id, project_id,
        )):
            db.add(HiddenShadowProtocol(
                id=uuid4(), project_id=tenant, creator_id=uuid4(),
                request_id=uuid4(), request_fingerprint="a" * 64,
                window_start=now + timedelta(days=index + 1),
                window_end=now + timedelta(days=index + 2),
                sample_size=2, algorithm="sha256-seed-wecom-kf-independent-v3",
                random_seed="1a" * 16,
                created_at=now + timedelta(minutes=index),
                finalized_at=now,
                population_count=0, sample_count=0,
                population_sha256="a" * 64, manifest_sha256="b" * 64,
            ))
        db.flush()
        service = HiddenShadowProtocolService(db)
        first, more = service.list(project_id, limit=1)
        assert len(first) == 1 and more
        assert first[0].window_start == (
            now + timedelta(days=3)
        ).replace(tzinfo=None)
        all_rows, more = service.list(project_id, limit=2)
        assert len(all_rows) == 2 and not more


def test_future_protocol_freezes_reproducible_private_sample(monkeypatch):
    engine = create_engine("sqlite://")
    ShadowReviewProtocol.__table__.create(engine)
    HiddenShadowProtocol.__table__.create(engine)
    HiddenShadowProtocolCase.__table__.create(engine)
    now = datetime.now(timezone.utc)
    project_id, creator_id, request_id = (uuid4() for _ in range(3))
    candidate_a, candidate_b = uuid4(), uuid4()
    pair_a = HiddenShadowComparison(
        candidate_id=candidate_a, visitor_id=uuid4(),
        staff_delivery_id=uuid4(), candidate_sha256="a" * 64,
        source_message_id="source-a", source_content_sha256="1" * 64,
        staff_reply_sha256="b" * 64,
        candidate_completed_at=now + timedelta(minutes=2),
        staff_reply_created_at=now + timedelta(minutes=3),
    )
    pair_b = HiddenShadowComparison(
        candidate_id=candidate_b, visitor_id=uuid4(),
        staff_delivery_id=uuid4(), candidate_sha256="c" * 64,
        source_message_id="source-b", source_content_sha256="2" * 64,
        staff_reply_sha256="d" * 64,
        candidate_completed_at=now + timedelta(minutes=2),
        staff_reply_created_at=now + timedelta(minutes=3),
    )
    seed = "1a" * 16
    monkeypatch.setattr(
        "app.services.hidden_shadow_protocol.secrets.token_hex",
        lambda _count: seed,
    )
    monkeypatch.setattr(
        "app.services.hidden_shadow_protocol.independent_shadow_comparisons",
        lambda *_args, **_kwargs: (
            item for item in (pair_b, pair_a)
        ),
    )
    start, end = now + timedelta(minutes=1), now + timedelta(hours=1)
    with Session(engine) as db:
        service = HiddenShadowProtocolService(db)
        row = service.register(
            project_id=project_id, creator_id=creator_id,
            request_id=request_id, window_start=start, window_end=end,
            sample_size=1, now=now,
        )
        replay = service.register(
            project_id=project_id, creator_id=creator_id,
            request_id=request_id, window_start=start, window_end=end,
            sample_size=1, now=now,
        )
        assert replay.id == row.id
        assert row.finalized_at is None
        assert row.seed_sha256 == sha256(bytes.fromhex(seed)).hexdigest()
        with pytest.raises(TGOAPIException) as early:
            service.finalize(project_id, row.id, now=now)
        assert early.value.code == "HIDDEN_PROTOCOL_NOT_READY"

        row.random_seed = "2b" * 16
        db.commit()
        with pytest.raises(TGOAPIException) as changed_seed:
            service.finalize(
                project_id, row.id, now=end + timedelta(seconds=1),
            )
        assert changed_seed.value.code == (
            "HIDDEN_PROTOCOL_REGISTRATION_CHANGED"
        )
        row.random_seed = seed
        db.commit()

        frozen = service.finalize(
            project_id, row.id, now=end + timedelta(seconds=1),
        )
        with pytest.raises(TGOAPIException) as cannot_cancel_frozen:
            service.cancel(project_id, row.id, actor_id=creator_id)
        assert cannot_cancel_frozen.value.code == (
            "HIDDEN_PROTOCOL_ALREADY_FROZEN"
        )
        assert frozen.population_count == 2
        assert frozen.sample_count == 1
        assert len(frozen.population_sha256) == 64
        assert len(frozen.manifest_sha256) == 64
        cases = db.scalars(select(HiddenShadowProtocolCase).where(
            HiddenShadowProtocolCase.protocol_id == row.id
        )).all()
        assert {case.candidate_id for case in cases} == {
            candidate_a, candidate_b,
        }
        rank = sorted(
            (candidate_a, candidate_b),
            key=lambda item: sha256(bytes.fromhex(seed) + item.bytes).digest(),
        )
        assert [case.candidate_id for case in cases
                if case.selected_position == 1] == [rank[0]]
        assert service.finalize(project_id, row.id).manifest_sha256 == (
            frozen.manifest_sha256
        )
        assert len(db.scalars(select(HiddenShadowProtocolCase)).all()) == 2
        integrity = service.verify_integrity(project_id, row.id)
        assert integrity.consistent is True
        assert integrity.metadata_valid is True
        assert integrity.population_count == 2
        assert integrity.recorded_population_count == 2
        row.window_start = start + timedelta(seconds=5)
        db.commit()
        assert service.verify_integrity(
            project_id, row.id,
        ).metadata_valid is False
        row.window_start = start
        db.commit()
        assert service.verify_integrity(project_id, row.id).consistent is True

        row.random_seed = "2b" * 16
        db.commit()
        assert service.verify_integrity(
            project_id, row.id,
        ).metadata_valid is False
        row.random_seed = seed
        db.commit()
        assert service.verify_integrity(project_id, row.id).consistent is True

        row.seed_sha256 = None
        db.commit()
        missing_commitment = service.verify_integrity(project_id, row.id)
        assert missing_commitment.metadata_valid is False
        assert missing_commitment.consistent is False
        row.seed_sha256 = sha256(bytes.fromhex(seed)).hexdigest()
        db.commit()
        assert service.verify_integrity(project_id, row.id).consistent is True
        selected_case = next(
            case for case in cases if case.selected_position == 1
        )
        original_reply_hash = selected_case.staff_reply_sha256
        selected_case.staff_reply_sha256 = "e" * 64
        db.commit()
        tampered_reply = service.verify_integrity(project_id, row.id)
        assert tampered_reply.consistent is False
        assert tampered_reply.manifest_valid is False
        assert tampered_reply.population_valid is True
        selected_case.staff_reply_sha256 = "not-a-sha256"
        db.commit()
        invalid_reply_hash = service.verify_integrity(project_id, row.id)
        assert invalid_reply_hash.consistent is False
        assert invalid_reply_hash.manifest_valid is False
        assert invalid_reply_hash.manifest_sha256 is None
        selected_case.staff_reply_sha256 = original_reply_hash
        selected_case.selected_position = None
        db.commit()
        tampered_sample = service.verify_integrity(project_id, row.id)
        assert tampered_sample.consistent is False
        assert tampered_sample.manifest_valid is True
        assert tampered_sample.sample_valid is False
        selected_case.selected_position = 1
        db.commit()
        assert service.verify_integrity(project_id, row.id).consistent is True
        original_source_hash = selected_case.source_content_sha256
        selected_case.source_content_sha256 = "3" * 64
        db.commit()
        assert service.verify_integrity(
            project_id, row.id,
        ).manifest_valid is False
        selected_case.source_content_sha256 = original_source_hash
        db.commit()
        selected_page = service.list_cases(
            project_id, row.id, limit=1, selected_only=True,
        )
        assert selected_page.total_cases == 1
        assert len(selected_page.items) == 1
        assert selected_page.items[0].selected_position == 1
        population_page = service.list_cases(
            project_id, row.id, limit=1, selected_only=False,
        )
        assert population_page.total_cases == 2
        assert population_page.next_cursor is not None
        second_page = service.list_cases(
            project_id, row.id, limit=1, selected_only=False,
            after_candidate_id=population_page.next_cursor,
        )
        assert len(second_page.items) == 1
        with pytest.raises(TGOAPIException) as other_project:
            service.get(uuid4(), row.id)
        assert other_project.value.status_code == 404
    engine.dispose()


def test_active_protocol_and_reused_request_cannot_change_conditions():
    engine = create_engine("sqlite://")
    ShadowReviewProtocol.__table__.create(engine)
    HiddenShadowProtocol.__table__.create(engine)
    now = datetime.now(timezone.utc)
    project_id, creator_id, request_id = (uuid4() for _ in range(3))
    with Session(engine) as db:
        service = HiddenShadowProtocolService(db)
        base = {
            "project_id": project_id, "creator_id": creator_id,
            "request_id": request_id,
            "window_start": now + timedelta(minutes=2),
            "window_end": now + timedelta(hours=1),
            "sample_size": 2, "now": now,
        }
        service.register(**base)
        with pytest.raises(TGOAPIException) as changed:
            service.register(**{**base, "sample_size": 3})
        assert changed.value.code == "HIDDEN_PROTOCOL_REQUEST_CONFLICT"
        with pytest.raises(TGOAPIException) as active:
            service.register(**{**base, "request_id": uuid4()})
        assert active.value.code == "HIDDEN_PROTOCOL_ACTIVE"
    engine.dispose()


def test_cancel_preserves_legacy_registration_and_frees_project_slot():
    engine = create_engine("sqlite://")
    ShadowReviewProtocol.__table__.create(engine)
    HiddenShadowProtocol.__table__.create(engine)
    now = datetime.now(timezone.utc)
    project_id, actor_id, request_id = (uuid4() for _ in range(3))
    with Session(engine) as db:
        service = HiddenShadowProtocolService(db)
        request = {
            "project_id": project_id, "creator_id": actor_id,
            "window_start": now + timedelta(minutes=2),
            "window_end": now + timedelta(hours=1),
            "sample_size": 2, "now": now,
        }
        legacy = service.register(request_id=request_id, **request)
        legacy.seed_sha256 = None
        db.commit()

        cancelled = service.cancel(
            project_id, legacy.id, actor_id=actor_id,
            now=now + timedelta(seconds=1),
        )
        assert cancelled.cancelled_at is not None
        assert cancelled.cancelled_by == actor_id
        assert service.cancel(
            project_id, legacy.id, actor_id=actor_id,
        ).cancelled_at == cancelled.cancelled_at
        with pytest.raises(TGOAPIException) as cannot_freeze:
            service.finalize(
                project_id, legacy.id, now=now + timedelta(hours=2),
            )
        assert cannot_freeze.value.code == "HIDDEN_PROTOCOL_CANCELLED"
        with pytest.raises(TGOAPIException) as cannot_reuse:
            service.register(request_id=request_id, **request)
        assert cannot_reuse.value.code == "HIDDEN_PROTOCOL_REQUEST_CONFLICT"

        replacement = service.register(request_id=uuid4(), **request)
        assert replacement.id != legacy.id
        assert legacy.cancelled_at is not None
        with pytest.raises(TGOAPIException) as wrong_tenant:
            service.cancel(uuid4(), replacement.id, actor_id=actor_id)
        assert wrong_tenant.value.status_code == 404
    engine.dispose()


def test_freeze_rejects_malformed_source_digest(monkeypatch):
    engine = create_engine("sqlite://")
    ShadowReviewProtocol.__table__.create(engine)
    HiddenShadowProtocol.__table__.create(engine)
    now = datetime.now(timezone.utc)
    pair = HiddenShadowComparison(
        candidate_id=uuid4(), visitor_id=uuid4(),
        staff_delivery_id=uuid4(), source_message_id="source-bad",
        source_content_sha256="not-a-sha256",
        candidate_sha256="a" * 64, staff_reply_sha256="b" * 64,
        candidate_completed_at=now, staff_reply_created_at=now,
    )
    monkeypatch.setattr(
        "app.services.hidden_shadow_protocol.independent_shadow_comparisons",
        lambda *_args, **_kwargs: (item for item in (pair,)),
    )
    with Session(engine) as db:
        service = HiddenShadowProtocolService(db)
        protocol = service.register(
            project_id=uuid4(), creator_id=uuid4(), request_id=uuid4(),
            window_start=now + timedelta(minutes=1),
            window_end=now + timedelta(hours=1),
            sample_size=1, now=now,
        )
        with pytest.raises(TGOAPIException) as invalid:
            service.finalize(
                protocol.project_id, protocol.id,
                now=now + timedelta(hours=2),
            )
        assert invalid.value.code == "HIDDEN_PROTOCOL_EVIDENCE_INVALID"
    engine.dispose()
