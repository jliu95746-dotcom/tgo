"""Fail-closed governance policy and review lifecycle for RAG knowledge."""

from datetime import datetime
from typing import Sequence
from uuid import UUID

from sqlalchemy import ColumnElement, Select, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.documents import FileDocument
from ..models.files import File
from ..models.knowledge_governance import KnowledgeGovernanceRecord
from ..models.qa import QAPair
from ..schemas.knowledge_governance import (
    AutomaticAnswerEligibility,
    AutomaticAnswerEligibilityReason,
    KnowledgeChannel,
    KnowledgeDocumentType,
    KnowledgeGovernanceBackfillRequest,
    KnowledgeGovernanceBackfillResponse,
    KnowledgeGovernanceDraftRequest,
    KnowledgeGovernanceInput,
    KnowledgeGovernanceListResponse,
    KnowledgeGovernanceRecordResponse,
    KnowledgeReviewDecision,
    KnowledgeReviewStatus,
    KnowledgeSourceOrigin,
)
from ..schemas.common import PaginationMetadata


class InvalidReviewTransitionError(ValueError):
    """Raised when a review lifecycle transition is not permitted."""


class KnowledgeGovernanceNotFoundError(LookupError):
    """Raised when a source or governance record is outside the project scope."""


class KnowledgeGovernanceService:
    """Creates governance records and enforces review state transitions."""

    @staticmethod
    def new_record(
        project_id: UUID,
        data: KnowledgeGovernanceInput,
    ) -> KnowledgeGovernanceRecord:
        """Map a validated contract to a persistence record."""
        return KnowledgeGovernanceRecord(
            project_id=project_id,
            file_id=data.file_id,
            qa_pair_id=data.qa_pair_id,
            document_type=data.document_type.value,
            product_line=data.product_line,
            channels=[channel.value for channel in data.channels],
            effective_at=data.effective_at,
            expires_at=data.expires_at,
            owner=data.owner,
            document_version=data.document_version,
            allow_automatic_reply=data.allow_automatic_reply,
            review_status=data.review_status.value,
            reviewed_by=data.reviewed_by,
            reviewed_at=data.reviewed_at,
            source_origin=data.source_origin.value,
        )

    @staticmethod
    def submit_for_review(record: KnowledgeGovernanceRecord) -> None:
        """Move an editable record into pending review."""
        allowed = {
            KnowledgeReviewStatus.DRAFT.value,
            KnowledgeReviewStatus.REJECTED.value,
        }
        if record.review_status not in allowed:
            raise InvalidReviewTransitionError(
                f"cannot submit knowledge in status {record.review_status!r}"
            )
        record.review_status = KnowledgeReviewStatus.PENDING_REVIEW.value
        record.reviewed_by = None
        record.reviewed_at = None

    @staticmethod
    def update_draft(
        record: KnowledgeGovernanceRecord,
        data: KnowledgeGovernanceDraftRequest,
    ) -> None:
        """Update editable metadata and return rejected records to draft."""
        allowed = {
            KnowledgeReviewStatus.DRAFT.value,
            KnowledgeReviewStatus.REJECTED.value,
        }
        if record.review_status not in allowed:
            raise InvalidReviewTransitionError(
                f"cannot edit knowledge in status {record.review_status!r}"
            )
        record.document_type = data.document_type.value
        record.product_line = data.product_line
        record.channels = [channel.value for channel in data.channels]
        record.effective_at = data.effective_at
        record.expires_at = data.expires_at
        record.owner = data.owner
        record.document_version = data.document_version
        record.allow_automatic_reply = data.allow_automatic_reply
        record.source_origin = data.source_origin.value
        record.review_status = KnowledgeReviewStatus.DRAFT.value
        record.reviewed_by = None
        record.reviewed_at = None

    @staticmethod
    def apply_review_decision(
        record: KnowledgeGovernanceRecord,
        decision: KnowledgeReviewDecision,
    ) -> None:
        """Apply an auditable approval/rejection/revocation transition."""
        current = record.review_status
        target = decision.status.value
        normal_review = (
            current == KnowledgeReviewStatus.PENDING_REVIEW.value
            and target
            in {
                KnowledgeReviewStatus.APPROVED.value,
                KnowledgeReviewStatus.REJECTED.value,
            }
        )
        revocation = (
            current == KnowledgeReviewStatus.APPROVED.value
            and target == KnowledgeReviewStatus.REVOKED.value
        )
        if not (normal_review or revocation):
            raise InvalidReviewTransitionError(
                f"review transition {current!r} -> {target!r} is not allowed"
            )
        record.review_status = target
        record.reviewed_by = decision.reviewer
        record.reviewed_at = decision.reviewed_at

    @staticmethod
    async def list_records(
        db: AsyncSession,
        *,
        project_id: UUID,
        collection_id: UUID,
        review_status: KnowledgeReviewStatus | None,
        limit: int,
        offset: int,
    ) -> KnowledgeGovernanceListResponse:
        """List file governance records within one collection."""
        filters = (
            KnowledgeGovernanceRecord.project_id == project_id,
            KnowledgeGovernanceRecord.deleted_at.is_(None),
            KnowledgeGovernanceRecord.file_id == File.id,
            File.project_id == project_id,
            File.collection_id == collection_id,
            File.deleted_at.is_(None),
        )
        statement = select(KnowledgeGovernanceRecord, File).where(*filters)
        count_statement = (
            select(func.count(KnowledgeGovernanceRecord.id))
            .select_from(KnowledgeGovernanceRecord, File)
            .where(*filters)
        )
        if review_status is not None:
            status_filter = (
                KnowledgeGovernanceRecord.review_status == review_status.value
            )
            statement = statement.where(status_filter)
            count_statement = count_statement.where(status_filter)

        total = int((await db.execute(count_statement)).scalar_one())
        rows = (
            await db.execute(
                statement
                .order_by(KnowledgeGovernanceRecord.updated_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
        data = tuple(
            KnowledgeGovernanceService._record_response(record, file_record)
            for record, file_record in rows
        )
        return KnowledgeGovernanceListResponse(
            data=data,
            pagination=PaginationMetadata(
                total=total,
                limit=limit,
                offset=offset,
                has_next=offset + limit < total,
                has_prev=offset > 0,
            ),
        )

    @staticmethod
    async def upsert_file_draft(
        db: AsyncSession,
        *,
        project_id: UUID,
        file_id: UUID,
        data: KnowledgeGovernanceDraftRequest,
    ) -> KnowledgeGovernanceRecordResponse:
        """Create or update governance metadata for a project-owned file."""
        file_record = await KnowledgeGovernanceService._get_file(
            db,
            project_id=project_id,
            file_id=file_id,
        )
        record = (
            await db.execute(
                select(KnowledgeGovernanceRecord).where(
                    KnowledgeGovernanceRecord.project_id == project_id,
                    KnowledgeGovernanceRecord.file_id == file_id,
                    KnowledgeGovernanceRecord.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if record is None:
            record = KnowledgeGovernanceService.new_record(
                project_id,
                KnowledgeGovernanceInput(
                    file_id=file_id,
                    document_type=data.document_type,
                    product_line=data.product_line,
                    channels=data.channels,
                    effective_at=data.effective_at,
                    expires_at=data.expires_at,
                    owner=data.owner,
                    document_version=data.document_version,
                    allow_automatic_reply=data.allow_automatic_reply,
                    review_status=KnowledgeReviewStatus.DRAFT,
                    source_origin=data.source_origin,
                ),
            )
            db.add(record)
        else:
            KnowledgeGovernanceService.update_draft(record, data)
        await db.commit()
        await db.refresh(record)
        return KnowledgeGovernanceService._record_response(record, file_record)

    @staticmethod
    async def submit_record(
        db: AsyncSession,
        *,
        project_id: UUID,
        record_id: UUID,
    ) -> KnowledgeGovernanceRecordResponse:
        """Submit a draft or rejected record for review."""
        record, file_record = await KnowledgeGovernanceService._get_record_with_file(
            db,
            project_id=project_id,
            record_id=record_id,
        )
        KnowledgeGovernanceService.submit_for_review(record)
        await db.commit()
        await db.refresh(record)
        return KnowledgeGovernanceService._record_response(record, file_record)

    @staticmethod
    async def review_record(
        db: AsyncSession,
        *,
        project_id: UUID,
        record_id: UUID,
        decision: KnowledgeReviewDecision,
    ) -> KnowledgeGovernanceRecordResponse:
        """Apply an audited review decision to a project-owned record."""
        record, file_record = await KnowledgeGovernanceService._get_record_with_file(
            db,
            project_id=project_id,
            record_id=record_id,
        )
        KnowledgeGovernanceService.apply_review_decision(record, decision)
        await db.commit()
        await db.refresh(record)
        return KnowledgeGovernanceService._record_response(record, file_record)

    @staticmethod
    async def backfill_files(
        db: AsyncSession,
        *,
        project_id: UUID,
        data: KnowledgeGovernanceBackfillRequest,
    ) -> KnowledgeGovernanceBackfillResponse:
        """Attach draft, automatic-answer-disabled records to legacy files."""
        rows = (
            await db.execute(
                select(File, KnowledgeGovernanceRecord.id)
                .outerjoin(
                    KnowledgeGovernanceRecord,
                    (KnowledgeGovernanceRecord.file_id == File.id)
                    & (KnowledgeGovernanceRecord.deleted_at.is_(None)),
                )
                .where(
                    File.project_id == project_id,
                    File.collection_id == data.collection_id,
                    File.deleted_at.is_(None),
                )
                .order_by(File.created_at.asc())
            )
        ).all()
        missing_files = [file_record for file_record, record_id in rows if record_id is None]
        if not data.dry_run:
            for file_record in missing_files:
                db.add(
                    KnowledgeGovernanceService.new_record(
                        project_id,
                        KnowledgeGovernanceInput(
                            file_id=file_record.id,
                            document_type=data.document_type,
                            product_line=data.product_line,
                            channels=data.channels,
                            effective_at=data.effective_at,
                            expires_at=data.expires_at,
                            owner=data.owner,
                            document_version=data.document_version,
                            allow_automatic_reply=False,
                            review_status=KnowledgeReviewStatus.DRAFT,
                            source_origin=data.source_origin,
                        ),
                    )
                )
            await db.commit()
        return KnowledgeGovernanceBackfillResponse(
            scanned_count=len(rows),
            missing_count=len(missing_files),
            created_count=0 if data.dry_run else len(missing_files),
            dry_run=data.dry_run,
        )

    @staticmethod
    async def _get_file(
        db: AsyncSession,
        *,
        project_id: UUID,
        file_id: UUID,
    ) -> File:
        file_record = (
            await db.execute(
                select(File).where(
                    File.id == file_id,
                    File.project_id == project_id,
                    File.deleted_at.is_(None),
                    File.collection_id.is_not(None),
                )
            )
        ).scalar_one_or_none()
        if file_record is None:
            raise KnowledgeGovernanceNotFoundError("file not found")
        if file_record.collection_id is None:
            raise KnowledgeGovernanceNotFoundError("file is not attached to a collection")
        return file_record

    @staticmethod
    async def _get_record_with_file(
        db: AsyncSession,
        *,
        project_id: UUID,
        record_id: UUID,
    ) -> tuple[KnowledgeGovernanceRecord, File]:
        row = (
            await db.execute(
                select(KnowledgeGovernanceRecord, File).where(
                    KnowledgeGovernanceRecord.id == record_id,
                    KnowledgeGovernanceRecord.project_id == project_id,
                    KnowledgeGovernanceRecord.deleted_at.is_(None),
                    KnowledgeGovernanceRecord.file_id == File.id,
                    File.project_id == project_id,
                    File.deleted_at.is_(None),
                    File.collection_id.is_not(None),
                )
            )
        ).one_or_none()
        if row is None:
            raise KnowledgeGovernanceNotFoundError("governance record not found")
        return row[0], row[1]

    @staticmethod
    def _record_response(
        record: KnowledgeGovernanceRecord,
        file_record: File,
    ) -> KnowledgeGovernanceRecordResponse:
        source_origin = KnowledgeSourceOrigin(record.source_origin)
        return KnowledgeGovernanceRecordResponse(
            id=record.id,
            project_id=record.project_id,
            file_id=record.file_id,
            qa_pair_id=record.qa_pair_id,
            collection_id=file_record.collection_id,
            source_name=file_record.original_filename,
            document_type=KnowledgeDocumentType(record.document_type),
            product_line=record.product_line,
            channels=tuple(KnowledgeChannel(channel) for channel in record.channels),
            effective_at=record.effective_at,
            expires_at=record.expires_at,
            owner=record.owner,
            document_version=record.document_version,
            allow_automatic_reply=record.allow_automatic_reply,
            review_status=KnowledgeReviewStatus(record.review_status),
            reviewed_by=record.reviewed_by,
            reviewed_at=record.reviewed_at,
            source_origin=source_origin,
            content_is_untrusted=source_origin
            in {KnowledgeSourceOrigin.CUSTOMER, KnowledgeSourceOrigin.WEBSITE},
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class KnowledgeGovernancePolicy:
    """Central automatic-answer admission policy for governed knowledge."""

    @staticmethod
    def evaluate(
        data: KnowledgeGovernanceInput,
        *,
        at: datetime,
        channel: KnowledgeChannel,
        deleted_at: datetime | None = None,
    ) -> AutomaticAnswerEligibility:
        """Evaluate one record; every uncertain state is denied."""
        untrusted = data.content_is_untrusted
        if at.tzinfo is None or at.utcoffset() is None:
            return AutomaticAnswerEligibility(
                eligible=False,
                reason=AutomaticAnswerEligibilityReason.INVALID_TIME_CONTEXT,
                content_is_untrusted=untrusted,
            )
        if deleted_at is not None:
            reason = AutomaticAnswerEligibilityReason.DELETED
        elif data.review_status is not KnowledgeReviewStatus.APPROVED:
            reason = AutomaticAnswerEligibilityReason.NOT_APPROVED
        elif not data.allow_automatic_reply:
            reason = AutomaticAnswerEligibilityReason.AUTOMATIC_REPLY_DISABLED
        elif data.source_origin is KnowledgeSourceOrigin.CUSTOMER:
            reason = AutomaticAnswerEligibilityReason.CUSTOMER_CONTENT
        elif data.effective_at > at:
            reason = AutomaticAnswerEligibilityReason.NOT_YET_EFFECTIVE
        elif data.expires_at is not None and data.expires_at <= at:
            reason = AutomaticAnswerEligibilityReason.EXPIRED
        elif channel not in data.channels:
            reason = AutomaticAnswerEligibilityReason.CHANNEL_NOT_ALLOWED
        else:
            return AutomaticAnswerEligibility(
                eligible=True,
                reason=AutomaticAnswerEligibilityReason.ELIGIBLE,
                content_is_untrusted=untrusted,
            )
        return AutomaticAnswerEligibility(
            eligible=False,
            reason=reason,
            content_is_untrusted=untrusted,
        )

    @staticmethod
    def _database_gates(
        *,
        project_id: UUID,
        at: datetime,
        channel: KnowledgeChannel,
    ) -> tuple[ColumnElement[bool], ...]:
        governance = KnowledgeGovernanceRecord
        return (
            governance.project_id == project_id,
            governance.deleted_at.is_(None),
            governance.review_status == KnowledgeReviewStatus.APPROVED.value,
            governance.allow_automatic_reply.is_(True),
            governance.source_origin != KnowledgeSourceOrigin.CUSTOMER.value,
            governance.effective_at <= at,
            or_(governance.expires_at.is_(None), governance.expires_at > at),
            governance.channels.contains([channel.value]),
        )

    @classmethod
    def eligible_document_ids_statement(
        cls,
        *,
        project_id: UUID,
        at: datetime,
        channel: KnowledgeChannel,
        candidate_ids: Sequence[UUID] | None = None,
    ) -> Select[tuple[UUID]]:
        """Build the only SQL admission path for automatic-answer candidates."""
        gates = cls._database_gates(
            project_id=project_id,
            at=at,
            channel=channel,
        )
        file_governed = exists(
            select(KnowledgeGovernanceRecord.id)
            .join(File, KnowledgeGovernanceRecord.file_id == File.id)
            .where(
                File.id == FileDocument.file_id,
                File.project_id == project_id,
                File.deleted_at.is_(None),
                *gates,
            )
        )
        qa_governed = exists(
            select(KnowledgeGovernanceRecord.id)
            .join(QAPair, KnowledgeGovernanceRecord.qa_pair_id == QAPair.id)
            .where(
                QAPair.document_id == FileDocument.id,
                QAPair.project_id == project_id,
                QAPair.deleted_at.is_(None),
                *gates,
            )
        )
        statement = select(FileDocument.id).where(
            FileDocument.project_id == project_id,
            or_(file_governed, qa_governed),
        )
        if candidate_ids is not None:
            statement = statement.where(FileDocument.id.in_(candidate_ids))
        return statement
