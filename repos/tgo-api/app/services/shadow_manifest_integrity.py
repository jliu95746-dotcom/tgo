"""Recalculate one frozen G4 manifest and sample without changing evidence."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.assist_draft_record import AssistDraftRecord
from app.models.shadow_review_batch import (
    ShadowReviewBatch,
    ShadowReviewBatchItem,
)
from app.models.shadow_review_protocol import (
    ShadowReviewProtocol,
    ShadowReviewProtocolCandidate,
)
from app.schemas.shadow_sample import ShadowProtocolIntegrityResponse
from app.services.shadow_sample_selection import (
    SAMPLE_ALGORITHM,
    WECOM_SAMPLE_ALGORITHM,
    rank_shadow_population,
    wecom_case_exists,
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def verify_shadow_manifest(
    db: Session, project_id: UUID, protocol_id: UUID
) -> ShadowProtocolIntegrityResponse:
    protocol = db.scalar(
        select(ShadowReviewProtocol).where(
            ShadowReviewProtocol.id == protocol_id,
            ShadowReviewProtocol.project_id == project_id,
        )
    )
    if protocol is None:
        raise TGOAPIException(
            "正式影子验收方案不存在", "SHADOW_PROTOCOL_NOT_FOUND", status_code=404
        )
    if protocol.finalized_at is None:
        raise TGOAPIException(
            "正式影子验收候选清单尚未冻结",
            "SHADOW_PROTOCOL_NOT_READY",
            status_code=409,
        )
    if protocol.algorithm not in {SAMPLE_ALGORITHM, WECOM_SAMPLE_ALGORITHM}:
        raise TGOAPIException(
            "预登记抽样算法版本不受当前服务支持",
            "SHADOW_PROTOCOL_ALGORITHM_UNSUPPORTED",
            status_code=409,
        )

    candidates = db.execute(
        select(
            ShadowReviewProtocolCandidate.draft_id,
            ShadowReviewProtocolCandidate.visitor_id,
        )
        .where(ShadowReviewProtocolCandidate.protocol_id == protocol.id)
        .order_by(ShadowReviewProtocolCandidate.draft_id)
        .execution_options(stream_results=True)
    ).yield_per(1000)
    calculated = rank_shadow_population(
        ((item.draft_id, item.visitor_id) for item in candidates),
        sample_size=protocol.sample_size,
        random_seed=protocol.random_seed,
    )
    population_valid = (
        calculated.count == protocol.population_count
        and calculated.sha256 == protocol.population_sha256
    )
    # Older cycles without this digest remain explicitly unverifiable.
    manifest_valid = (
        protocol.manifest_sha256 is not None
        and calculated.manifest_sha256 == protocol.manifest_sha256
    )

    if calculated.count == 0:
        sample_valid = protocol.batch_id is None
        metadata_valid = protocol.batch_id is None
    else:
        batch = db.scalar(
            select(ShadowReviewBatch).where(
                ShadowReviewBatch.id == protocol.batch_id,
                ShadowReviewBatch.project_id == project_id,
            )
        )
        if batch is None:
            sample_valid = False
            metadata_valid = False
        else:
            actual = db.scalars(
                select(ShadowReviewBatchItem)
                .where(ShadowReviewBatchItem.batch_id == batch.id)
                .order_by(ShadowReviewBatchItem.position)
            ).all()
            expected = tuple(
                (position, draft_id, visitor_id)
                for position, (_, draft_id, visitor_id) in enumerate(
                    calculated.chosen, 1
                )
            )
            sample_valid = (
                tuple(
                    (item.position, item.draft_id, item.visitor_id)
                    for item in actual
                )
                == expected
            )
            metadata_valid = (
                batch.creator_id == protocol.creator_id
                and batch.request_fingerprint == protocol.request_fingerprint
                and _utc(batch.window_start) == _utc(protocol.window_start)
                and _utc(batch.window_end) == _utc(protocol.window_end)
                and batch.requested_size == protocol.sample_size
                and batch.sample_count == len(expected)
                and batch.population_count == protocol.population_count
                and batch.population_sha256 == protocol.population_sha256
                and batch.algorithm == protocol.algorithm
                and batch.random_seed == protocol.random_seed
            )

    if protocol.algorithm == WECOM_SAMPLE_ALGORITHM:
        invalid_channel = db.scalar(
            select(ShadowReviewProtocolCandidate.draft_id)
            .outerjoin(
                AssistDraftRecord,
                and_(
                    AssistDraftRecord.id
                    == ShadowReviewProtocolCandidate.draft_id,
                    AssistDraftRecord.project_id == project_id,
                ),
            )
            .where(
                ShadowReviewProtocolCandidate.protocol_id == protocol.id,
                or_(
                    AssistDraftRecord.id.is_(None),
                    AssistDraftRecord.visitor_id
                    != ShadowReviewProtocolCandidate.visitor_id,
                    ~wecom_case_exists(
                        project_id,
                        ShadowReviewProtocolCandidate.visitor_id,
                        AssistDraftRecord.source_message_id,
                        _utc(protocol.window_end),
                    ),
                ),
            )
            .limit(1)
        )
        metadata_valid = metadata_valid and invalid_channel is None

    return ShadowProtocolIntegrityResponse(
        protocol_id=protocol.id,
        consistent=(
            population_valid
            and manifest_valid
            and sample_valid
            and metadata_valid
        ),
        population_valid=population_valid,
        manifest_valid=manifest_valid,
        sample_valid=sample_valid,
        metadata_valid=metadata_valid,
        population_count=calculated.count,
        recorded_population_count=protocol.population_count,
        population_sha256=calculated.sha256,
        recorded_population_sha256=protocol.population_sha256,
        manifest_sha256=calculated.manifest_sha256,
        recorded_manifest_sha256=protocol.manifest_sha256,
    )
