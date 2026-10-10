"""Reuse automatic-answer admission for both runtime discovery and settings."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from ..models import Collection, FileDocument
from ..models.knowledge_governance import KnowledgeGovernanceRecord
from ..schemas.knowledge_availability import (
    ChannelUpdateRequest,
    CollectionAvailability,
    KnowledgeAvailability,
)
from ..schemas.knowledge_governance import (
    KnowledgeChannel,
    KnowledgeGovernanceInput,
    KnowledgeGovernanceRecordResponse,
)
from .knowledge_governance import (
    InvalidReviewTransitionError,
    KnowledgeGovernanceNotFoundError,
    KnowledgeGovernancePolicy,
    KnowledgeGovernanceService,
)


def collection_counts_statement(
    project: UUID, channel: KnowledgeChannel | str, at: datetime
) -> Select[tuple[UUID, int, int]]:
    admitted = KnowledgeGovernancePolicy.eligible_document_ids_statement(
        project_id=project,
        at=at,
        channel=KnowledgeChannel(channel),
    )
    return (
        select(
            FileDocument.collection_id,
            func.count(FileDocument.id),
            func.count(FileDocument.id).filter(FileDocument.id.in_(admitted)),
        )
        .where(FileDocument.project_id == project)
        .group_by(FileDocument.collection_id)
    )


async def knowledge_availability(
    db: AsyncSession, project: UUID, channel: KnowledgeChannel
) -> KnowledgeAvailability:
    at = datetime.now(UTC)
    collections = (
        await db.scalars(
            select(Collection)
            .where(
                Collection.project_id == project,
                Collection.deleted_at.is_(None),
            )
            .order_by(Collection.display_name)
        )
    ).all()
    counts = {
        row[0]: (row[1], row[2])
        for row in (
            await db.execute(
                collection_counts_statement(project, channel, at),
            )
        ).all()
    }
    reasons: dict[UUID, set[str]] = {}
    sources = (
        await db.execute(KnowledgeGovernanceService._source_statement(project))
    ).all()
    for record, file_record, pair in sources:
        source = file_record or pair
        data = KnowledgeGovernanceInput(
            file_id=record.file_id,
            qa_pair_id=record.qa_pair_id,
            document_type=record.document_type,
            product_line=record.product_line,
            channels=record.channels,
            effective_at=record.effective_at,
            expires_at=record.expires_at,
            owner=record.owner,
            document_version=record.document_version,
            allow_automatic_reply=record.allow_automatic_reply,
            review_status=record.review_status,
            reviewed_by=record.reviewed_by,
            reviewed_at=record.reviewed_at,
            source_origin=record.source_origin,
        )
        result = KnowledgeGovernancePolicy.evaluate(
            data, at=at, channel=channel
        )
        if not result.eligible:
            reasons.setdefault(source.collection_id, set()).add(
                result.reason.value
            )
    return KnowledgeAvailability(
        project_id=project,
        channel=channel,
        collections=[
            CollectionAvailability(
                id=c.id,
                name=c.display_name,
                total_chunk_count=counts.get(c.id, (0, 0))[0],
                eligible_chunk_count=counts.get(c.id, (0, 0))[1],
                blocked_reasons=sorted(reasons.get(c.id, set())),
            )
            for c in collections
        ],
    )


def apply_channel_update(
    record: KnowledgeGovernanceRecord,
    request: ChannelUpdateRequest,
    *,
    reviewer: str,
    at: datetime,
) -> None:
    if record.review_status != "approved":
        raise InvalidReviewTransitionError("只有已审核通过的资料可以调整适用渠道")
    if record.updated_at != request.expected_updated_at:
        raise InvalidReviewTransitionError("资料设置已变化，请刷新后重试")
    record.channels = [c.value for c in request.channels]
    record.reviewed_by, record.reviewed_at, record.updated_at = (
        reviewer,
        at,
        at,
    )


async def update_channels(
    db: AsyncSession,
    project: UUID,
    record_id: UUID,
    request: ChannelUpdateRequest,
    reviewer: str,
) -> KnowledgeGovernanceRecordResponse:
    record = (
        await db.scalars(
            select(KnowledgeGovernanceRecord)
            .where(
                KnowledgeGovernanceRecord.project_id == project,
                KnowledgeGovernanceRecord.id == record_id,
                KnowledgeGovernanceRecord.deleted_at.is_(None),
            )
            .with_for_update()
        )
    ).one_or_none()
    if record is None:
        raise KnowledgeGovernanceNotFoundError("governance record not found")
    record, source = await KnowledgeGovernanceService._get_record_with_file(
        db,
        project_id=project,
        record_id=record_id,
    )
    apply_channel_update(
        record, request, reviewer=reviewer, at=datetime.now(UTC)
    )
    await db.flush()
    return KnowledgeGovernanceService._record_response(record, source)
