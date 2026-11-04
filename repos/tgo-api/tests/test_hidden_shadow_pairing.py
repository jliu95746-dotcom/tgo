"""Select only independent, confirmed human replies for G4."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import (
    Boolean, Column, DateTime, MetaData, String, Table, Uuid,
    create_engine,
)
from sqlalchemy.orm import Session

from app.models.assist_draft_record import AssistDraftRecord
from app.models.hidden_shadow_run import HiddenShadowRun
from app.models.staff_message_delivery import StaffMessageDelivery
from app.services.hidden_shadow_pairing import independent_shadow_comparisons
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE


def test_hidden_pair_requires_confirmed_reply_after_candidate_and_no_draft():
    engine = create_engine("sqlite://")
    platforms = Table(
        "api_platforms", MetaData(),
        Column("id", Uuid(), primary_key=True),
        Column("project_id", Uuid(), nullable=False),
        Column("type", String(20), nullable=False),
        Column("is_active", Boolean(), nullable=False),
        Column("deleted_at", DateTime(timezone=True)),
    )
    platforms.create(engine)
    for model in (HiddenShadowRun, AssistDraftRecord, StaffMessageDelivery):
        model.__table__.create(engine)
    project_id, platform_id, visitor_id, staff_id = (uuid4() for _ in range(4))
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        db.execute(platforms.insert().values(
            id=platform_id, project_id=project_id,
            type="wecom", is_active=True,
        ))
        candidate = HiddenShadowRun(
            project_id=project_id, platform_id=platform_id,
            visitor_id=visitor_id, source_message_id="source-1",
            source_message_seq=1,
            source_verified_at=now - timedelta(minutes=4),
            source_content_sha256="a" * 64,
            generation_version="wecom-kf-knowledge-only-v1",
            status="ready", attempt_count=1,
            knowledge_evidence_sha256="b" * 64,
            ai_execution_id=uuid4(),
            answer="AI 独立回答",
            answer_sha256=sha256("AI 独立回答".encode()).hexdigest(),
            created_at=now - timedelta(minutes=4),
            completed_at=now - timedelta(minutes=3),
        )
        delivery = StaffMessageDelivery(
            project_id=project_id, staff_id=staff_id,
            visitor_id=visitor_id, channel_id="visitor-vtr",
            channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
            client_msg_no="staff-message-1",
            source_message_id="source-1", request_fingerprint="c" * 64,
            payload={"type": 1, "content": "人工独立回答"},
            external_status="sent", history_status="sent",
            created_at=now - timedelta(minutes=2),
            updated_at=now - timedelta(minutes=2),
        )
        db.add_all([candidate, delivery])
        db.commit()
        kwargs = {
            "window_start": now - timedelta(days=1),
            "window_end": now + timedelta(minutes=1),
        }

        def matches():
            return list(independent_shadow_comparisons(
                db, project_id, **kwargs,
            ))

        pairs = matches()
        assert len(pairs) == 1
        assert pairs[0].candidate_id == candidate.id
        assert pairs[0].staff_delivery_id == delivery.id
        assert pairs[0].staff_reply_sha256 == sha256(
            "人工独立回答".encode()
        ).hexdigest()

        delivery.external_status = "failed"
        db.commit()
        assert matches() == []
        delivery.external_status = "not_required"
        delivery.history_status = "sent"
        db.commit()
        assert matches() == []
        delivery.external_status = "sent"
        delivery.created_at = now - timedelta(minutes=5)
        db.commit()
        assert matches() == []
        delivery.created_at = now - timedelta(minutes=2)
        visible_draft = AssistDraftRecord(
            project_id=project_id, staff_id=staff_id,
            visitor_id=visitor_id, request_id=uuid4(),
            source_message_id="source-1", request_fingerprint="d" * 64,
            response_data={"draft": "员工可见草稿"},
            created_at=now - timedelta(minutes=3),
        )
        db.add(visible_draft)
        db.commit()
        assert matches() == []

        visible_draft.created_at = now - timedelta(minutes=1)
        db.commit()
        assert len(matches()) == 1
    engine.dispose()
