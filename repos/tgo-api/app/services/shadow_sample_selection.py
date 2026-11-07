"Shared, reproducible candidate selection for exploratory and official \
G4 batches."

from __future__ import annotations

import hashlib
import heapq
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session
from sqlalchemy.sql.elements import ColumnElement

from app.models.assist_draft_record import AssistDraftRecord
from app.models.message_intent_result import MessageIntentResult
from app.models.platform import Platform
from app.models.staff_message_delivery import StaffMessageDelivery
from app.models.visitor import Visitor
from app.services.shadow_review_audit import _confirmed_delivery

SAMPLE_ALGORITHM = "sha256-seed-draft-v1"
WECOM_SAMPLE_ALGORITHM = "sha256-seed-wecom-kf-v2"


@dataclass(frozen=True)
class ShadowPopulation:
    count: int
    sha256: str
    manifest_sha256: str
    chosen: tuple[tuple[bytes, UUID, UUID], ...]


def wecom_case_exists(
    project_id: UUID,
    visitor_id: InstrumentedAttribute[UUID],
    source_message_id: InstrumentedAttribute[str | None],
    window_end: datetime,
) -> ColumnElement[bool]:
    (
        'Require one current project-owned WeCom KF source and shadow '
        'analysis.'
    )
    return (
        select(Visitor.id)
        .join(
            Platform,
            and_(
                Platform.id == Visitor.platform_id,
                Platform.project_id == Visitor.project_id,
            ),
        )
        .join(
            MessageIntentResult,
            and_(
                MessageIntentResult.project_id == Visitor.project_id,
                MessageIntentResult.visitor_id == Visitor.id,
                MessageIntentResult.platform_id == Platform.id,
                MessageIntentResult.source_message_id == source_message_id,
                MessageIntentResult.analysis_mode == "shadow",
                MessageIntentResult.created_at < window_end,
            ),
        )
        .where(
            Visitor.id == visitor_id,
            Visitor.project_id == project_id,
            Visitor.deleted_at.is_(None),
            Platform.type == "wecom",
            Platform.is_active.is_(True),
            Platform.deleted_at.is_(None),
        )
        .exists()
    )


def eligible_shadow_cases_query(
    project_id: UUID,
    *,
    window_start: datetime,
    window_end: datetime,
    target_channel: str | None = None,
) -> Select[tuple[UUID, UUID]]:
    """Build one eligibility predicate for exploratory and official samples."""
    query = select(AssistDraftRecord.id, AssistDraftRecord.visitor_id).where(
        AssistDraftRecord.project_id == project_id,
        AssistDraftRecord.created_at >= window_start,
        AssistDraftRecord.created_at < window_end,
        AssistDraftRecord.source_message_id.is_not(None),
        AssistDraftRecord.source_proof["status"].as_string() == "verified",
        func.length(
            func.trim(AssistDraftRecord.response_data["draft"].as_string())
        )
        > 0,
        select(MessageIntentResult.id)
        .where(
            MessageIntentResult.project_id == AssistDraftRecord.project_id,
            MessageIntentResult.visitor_id == AssistDraftRecord.visitor_id,
            MessageIntentResult.source_message_id
            == AssistDraftRecord.source_message_id,
            MessageIntentResult.analysis_mode == "shadow",
            MessageIntentResult.created_at < window_end,
        )
        .exists(),
        select(StaffMessageDelivery.id)
        .where(
            StaffMessageDelivery.project_id == AssistDraftRecord.project_id,
            StaffMessageDelivery.visitor_id == AssistDraftRecord.visitor_id,
            StaffMessageDelivery.staff_id == AssistDraftRecord.staff_id,
            StaffMessageDelivery.source_message_id
            == AssistDraftRecord.source_message_id,
            StaffMessageDelivery.assist_draft_id == AssistDraftRecord.id,
            StaffMessageDelivery.created_at >= AssistDraftRecord.created_at,
            StaffMessageDelivery.created_at < window_end,
            StaffMessageDelivery.updated_at < window_end,
            _confirmed_delivery(),
            StaffMessageDelivery.payload["type"].as_integer().in_((1, 12)),
            func.length(
                func.trim(StaffMessageDelivery.payload["content"].as_string())
            )
            > 0,
        )
        .exists(),
    )
    if target_channel == "wecom_kf":
        query = query.where(
            wecom_case_exists(
                project_id,
                AssistDraftRecord.visitor_id,
                AssistDraftRecord.source_message_id,
                window_end,
            )
        )
    elif target_channel is not None:
        raise ValueError("Unsupported shadow review channel")
    return query


def rank_shadow_population(
    rows: Iterable[tuple[UUID, UUID]], *, sample_size: int, random_seed: str
) -> ShadowPopulation:
    seed_bytes = bytes.fromhex(random_seed)
    population_hash = hashlib.sha256()
    manifest_hash = hashlib.sha256()
    population_count = 0

    def ranked_candidates() -> Iterator[tuple[bytes, UUID, UUID]]:
        nonlocal population_count
        for draft_id, visitor_id in rows:
            population_count += 1
            population_hash.update(draft_id.bytes)
            manifest_hash.update(draft_id.bytes + visitor_id.bytes)
            yield (
                hashlib.sha256(seed_bytes + draft_id.bytes).digest(),
                draft_id,
                visitor_id,
            )

    chosen = heapq.nsmallest(sample_size, ranked_candidates())
    return ShadowPopulation(
        count=population_count,
        sha256=population_hash.hexdigest(),
        manifest_sha256=manifest_hash.hexdigest(),
        chosen=tuple(chosen),
    )


def select_shadow_population(
    db: Session,
    project_id: UUID,
    *,
    window_start: datetime,
    window_end: datetime,
    sample_size: int,
    random_seed: str,
) -> ShadowPopulation:
    """Read and rank cases without persisting a manifest (exploration only)."""
    candidates = eligible_shadow_cases_query(
        project_id, window_start=window_start, window_end=window_end
    ).order_by(AssistDraftRecord.id)
    rows = db.execute(
        candidates.execution_options(stream_results=True)
    ).yield_per(1000)
    return rank_shadow_population(
        ((row.id, row.visitor_id) for row in rows),
        sample_size=sample_size,
        random_seed=random_seed,
    )
