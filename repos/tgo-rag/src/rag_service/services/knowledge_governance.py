"""Fail-closed governance policy and review lifecycle for RAG knowledge."""

from datetime import datetime
from typing import Sequence
from uuid import UUID

from sqlalchemy import ColumnElement, Select, exists, or_, select

from ..models.documents import FileDocument
from ..models.files import File
from ..models.knowledge_governance import KnowledgeGovernanceRecord
from ..models.qa import QAPair
from ..schemas.knowledge_governance import (
    AutomaticAnswerEligibility,
    AutomaticAnswerEligibilityReason,
    KnowledgeChannel,
    KnowledgeGovernanceInput,
    KnowledgeReviewDecision,
    KnowledgeReviewStatus,
    KnowledgeSourceOrigin,
)


class InvalidReviewTransitionError(ValueError):
    """Raised when a review lifecycle transition is not permitted."""


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
