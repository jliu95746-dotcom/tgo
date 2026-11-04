"""Validate frozen AI/human evidence and collect blind G4 assessments."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import NoReturn
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.hidden_shadow_protocol import HiddenShadowProtocolCase
from app.models.hidden_shadow_review import HiddenShadowCaseReview
from app.models.hidden_shadow_run import HiddenShadowRun
from app.models.staff_message_delivery import StaffMessageDelivery
from app.schemas.hidden_shadow_review import (
    HiddenShadowCaseDetail, HiddenShadowReviewReceipt,
    HiddenShadowReviewRequest, HiddenShadowReviewSummary,
    HiddenShadowSourceMessage,
)
from app.services.hidden_shadow_protocol import HiddenShadowProtocolService
from app.services.shadow_source_proof import (
    ShadowIMLookup, shadow_message_matches,
)
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import build_visitor_channel_id


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _fingerprint(
    protocol_id: UUID, candidate_id: UUID,
    request: HiddenShadowReviewRequest,
) -> str:
    encoded = json.dumps({
        "protocol_id": str(protocol_id),
        "candidate_id": str(candidate_id),
        "verdict": request.verdict,
        "reason_code": request.reason_code,
        "note": request.note,
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _receipt(row: HiddenShadowCaseReview) -> HiddenShadowReviewReceipt:
    return HiddenShadowReviewReceipt.model_validate(row, from_attributes=True)


class HiddenShadowReviewService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._protocols = HiddenShadowProtocolService(db)

    def _selected_case(
        self, project_id: UUID, protocol_id: UUID, candidate_id: UUID,
        *, lock: bool = False,
    ) -> tuple[HiddenShadowProtocolCase, UUID]:
        protocol = self._protocols.get(project_id, protocol_id)
        if protocol.finalized_at is None:
            raise TGOAPIException(
                "独立影子样本尚未冻结", "HIDDEN_REVIEW_NOT_READY",
                status_code=409,
            )
        query = select(HiddenShadowProtocolCase).where(
            HiddenShadowProtocolCase.protocol_id == protocol_id,
            HiddenShadowProtocolCase.candidate_id == candidate_id,
            HiddenShadowProtocolCase.selected_position.is_not(None),
        )
        if lock:
            query = query.with_for_update()
        case = self._db.scalar(query)
        if case is None:
            raise TGOAPIException(
                "独立影子抽样案例不存在", "HIDDEN_REVIEW_CASE_NOT_FOUND",
                status_code=404,
            )
        return case, protocol.creator_id

    def _detail(self, case: HiddenShadowProtocolCase,
                project_id: UUID) -> HiddenShadowCaseDetail:
        candidate = self._db.scalar(select(HiddenShadowRun).where(
            HiddenShadowRun.id == case.candidate_id,
            HiddenShadowRun.project_id == project_id,
        ))
        delivery = self._db.scalar(select(StaffMessageDelivery).where(
            StaffMessageDelivery.id == case.staff_delivery_id,
            StaffMessageDelivery.project_id == project_id,
        ))
        if candidate is None or delivery is None:
            self._evidence_changed()
        assert candidate is not None and delivery is not None
        if candidate.completed_at is None or candidate.answer is None:
            self._evidence_changed()
        assert candidate.completed_at is not None
        assert candidate.answer is not None
        reply = delivery.payload.get("content")
        if not isinstance(reply, str):
            self._evidence_changed()
        assert isinstance(reply, str)
        reply = reply.strip()
        if (
            candidate.status != "ready"
            or candidate.redacted_at is not None
            or not candidate.answer
            or candidate.answer_sha256 != case.candidate_sha256
            or hashlib.sha256(candidate.answer.encode()).hexdigest()
            != case.candidate_sha256
            or not reply
            or hashlib.sha256(reply.encode()).hexdigest()
            != case.staff_reply_sha256
            or candidate.visitor_id != case.visitor_id
            or delivery.visitor_id != case.visitor_id
            or candidate.source_message_id != case.source_message_id
            or candidate.source_content_sha256
            != case.source_content_sha256
            or candidate.source_message_id != delivery.source_message_id
            or delivery.assist_draft_id is not None
            or delivery.external_status != "sent"
            or _utc(candidate.completed_at)
            != _utc(case.candidate_completed_at)
            or _utc(delivery.created_at)
            != _utc(case.staff_reply_created_at)
            or _utc(candidate.completed_at) >= _utc(delivery.created_at)
        ):
            self._evidence_changed()
        evidence = json.dumps({
            "candidate_id": str(case.candidate_id),
            "source_message_id": candidate.source_message_id,
            "source_content_sha256": candidate.source_content_sha256,
            "candidate_sha256": case.candidate_sha256,
            "staff_delivery_id": str(case.staff_delivery_id),
            "staff_reply_sha256": case.staff_reply_sha256,
        }, sort_keys=True, separators=(",", ":")).encode()
        assert case.selected_position is not None
        return HiddenShadowCaseDetail(
            protocol_id=case.protocol_id,
            candidate_id=case.candidate_id,
            visitor_id=case.visitor_id,
            selected_position=case.selected_position,
            source_message_id=candidate.source_message_id,
            source_content_sha256=candidate.source_content_sha256,
            answer=candidate.answer,
            staff_reply=reply,
            evidence_sha256=hashlib.sha256(evidence).hexdigest(),
        )

    def get_case(
        self, project_id: UUID, protocol_id: UUID, candidate_id: UUID,
    ) -> HiddenShadowCaseDetail:
        case, _ = self._selected_case(project_id, protocol_id, candidate_id)
        return self._detail(case, project_id)

    async def get_source_message(
        self, project_id: UUID, protocol_id: UUID, candidate_id: UUID,
        *, im_client: ShadowIMLookup,
    ) -> HiddenShadowSourceMessage:
        """Read the live source only when it still matches frozen evidence."""
        case, _ = self._selected_case(project_id, protocol_id, candidate_id)
        candidate = self._db.scalar(select(HiddenShadowRun).where(
            HiddenShadowRun.id == candidate_id,
            HiddenShadowRun.project_id == project_id,
        ))
        if (
            candidate is None
            or candidate.visitor_id != case.visitor_id
            or candidate.source_message_id != case.source_message_id
            or candidate.source_content_sha256 != case.source_content_sha256
            or candidate.source_message_seq <= 0
            or candidate.source_verified_at is None
        ):
            self._evidence_changed()
        assert candidate is not None
        if not im_client.enabled:
            self._source_unavailable()
        try:
            message = await im_client.get_message_by_client_msg_no(
                channel_id=build_visitor_channel_id(case.visitor_id),
                channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
                client_msg_no=case.source_message_id,
                raise_on_error=True,
            )
        except Exception:
            self._source_unavailable()
        if message is None:
            self._evidence_changed()
        assert message is not None
        content = message.payload.get("content") if isinstance(
            message.payload, dict
        ) else None
        checked_at = datetime.now(timezone.utc)
        if (
            not isinstance(content, str)
            or not content.strip()
            or not shadow_message_matches(
                message,
                visitor_id=case.visitor_id,
                source_message_id=case.source_message_id,
                source_client_msg_no=case.source_message_id,
                customer_message=content,
                checked_at=checked_at,
            )
            or message.message_seq != candidate.source_message_seq
            or hashlib.sha256(content.strip().encode()).hexdigest()
            != case.source_content_sha256
        ):
            self._evidence_changed()
        return HiddenShadowSourceMessage(
            protocol_id=protocol_id,
            candidate_id=candidate_id,
            source_message_id=case.source_message_id,
            content=content.strip(),
            content_sha256=case.source_content_sha256,
            message_seq=message.message_seq,
            verified_at=checked_at,
        )

    def get_own_review(
        self, project_id: UUID, protocol_id: UUID, candidate_id: UUID,
        reviewer_id: UUID,
    ) -> HiddenShadowReviewReceipt | None:
        self._selected_case(project_id, protocol_id, candidate_id)
        row = self._db.scalar(select(HiddenShadowCaseReview).where(
            HiddenShadowCaseReview.project_id == project_id,
            HiddenShadowCaseReview.protocol_id == protocol_id,
            HiddenShadowCaseReview.candidate_id == candidate_id,
            HiddenShadowCaseReview.reviewer_id == reviewer_id,
        ))
        return _receipt(row) if row is not None else None

    def replay(
        self, project_id: UUID, protocol_id: UUID, candidate_id: UUID,
        reviewer_id: UUID, request: HiddenShadowReviewRequest,
    ) -> HiddenShadowReviewReceipt | None:
        """Replay identical requests before live IM revalidation."""
        _, creator_id = self._selected_case(
            project_id, protocol_id, candidate_id,
        )
        if creator_id == reviewer_id:
            raise TGOAPIException(
                "本批协调人不能复核自己的影子样本",
                "HIDDEN_REVIEW_COORDINATOR_CONFLICT", status_code=403,
            )
        row = self._db.scalar(select(HiddenShadowCaseReview).where(
            HiddenShadowCaseReview.project_id == project_id,
            HiddenShadowCaseReview.reviewer_id == reviewer_id,
            HiddenShadowCaseReview.request_id == request.request_id,
        ))
        if row is None:
            return None
        if row.request_fingerprint != _fingerprint(
            protocol_id, candidate_id, request,
        ):
            raise TGOAPIException(
                "复核请求编号已用于其他结论",
                "HIDDEN_REVIEW_REQUEST_CONFLICT", status_code=409,
            )
        return _receipt(row)

    def submit(
        self, project_id: UUID, protocol_id: UUID, candidate_id: UUID,
        reviewer_id: UUID, request: HiddenShadowReviewRequest,
        *, source_proof: HiddenShadowSourceMessage | None = None,
    ) -> HiddenShadowReviewReceipt:
        case, creator_id = self._selected_case(
            project_id, protocol_id, candidate_id, lock=True,
        )
        if creator_id == reviewer_id:
            raise TGOAPIException(
                "本批协调人不能复核自己的影子样本",
                "HIDDEN_REVIEW_COORDINATOR_CONFLICT", status_code=403,
            )
        fingerprint = _fingerprint(protocol_id, candidate_id, request)

        def prior() -> HiddenShadowCaseReview | None:
            row: HiddenShadowCaseReview | None = self._db.scalar(
                select(HiddenShadowCaseReview).where(
                    HiddenShadowCaseReview.project_id == project_id,
                    HiddenShadowCaseReview.reviewer_id == reviewer_id,
                    HiddenShadowCaseReview.request_id == request.request_id,
                )
            )
            if row is not None and row.request_fingerprint != fingerprint:
                raise TGOAPIException(
                    "复核请求编号已用于其他结论",
                    "HIDDEN_REVIEW_REQUEST_CONFLICT", status_code=409,
                )
            return row

        existing = prior()
        if existing is not None:
            return _receipt(existing)
        detail = self._detail(case, project_id)
        if source_proof is not None:
            candidate = self._db.scalar(select(HiddenShadowRun).where(
                HiddenShadowRun.id == candidate_id,
                HiddenShadowRun.project_id == project_id,
            ))
            checked_at = _utc(source_proof.verified_at)
            age_seconds = (
                datetime.now(timezone.utc) - checked_at
            ).total_seconds()
            proof_digest = hashlib.sha256(
                source_proof.content.strip().encode()
            ).hexdigest()
            if (
                candidate is None
                or source_proof.protocol_id != protocol_id
                or source_proof.candidate_id != candidate_id
                or source_proof.source_message_id != case.source_message_id
                or source_proof.content_sha256 != case.source_content_sha256
                or proof_digest != case.source_content_sha256
                or source_proof.message_seq != candidate.source_message_seq
                or not 0 <= age_seconds <= 60
            ):
                self._evidence_changed()
        reviews = self._db.scalars(
            select(HiddenShadowCaseReview).where(
                HiddenShadowCaseReview.protocol_id == protocol_id,
                HiddenShadowCaseReview.candidate_id == candidate_id,
            ).order_by(HiddenShadowCaseReview.created_at).limit(2)
        ).all()
        if any(
            row.evidence_sha256 != detail.evidence_sha256
            for row in reviews
        ):
            self._evidence_changed()
        if any(row.reviewer_id == reviewer_id for row in reviews):
            raise TGOAPIException(
                "本人已完成此案例复核，不能修改结论",
                "HIDDEN_REVIEW_ALREADY_SUBMITTED", status_code=409,
            )
        if len(reviews) >= 2:
            raise TGOAPIException(
                "此案例已完成两人独立复核",
                "HIDDEN_REVIEW_FULL", status_code=409,
            )
        row = HiddenShadowCaseReview(
            project_id=project_id, protocol_id=protocol_id,
            candidate_id=candidate_id, reviewer_id=reviewer_id,
            request_id=request.request_id, request_fingerprint=fingerprint,
            evidence_sha256=detail.evidence_sha256,
            source_verified_at=(
                source_proof.verified_at if source_proof is not None else None
            ),
            source_message_seq=(
                source_proof.message_seq if source_proof is not None else None
            ),
            source_content_sha256=(
                source_proof.content_sha256
                if source_proof is not None else None
            ),
            verdict=request.verdict, reason_code=request.reason_code,
            note=request.note,
        )
        self._db.add(row)
        try:
            self._db.commit()
        except IntegrityError:
            self._db.rollback()
            existing = prior()
            if existing is not None:
                return _receipt(existing)
            raise TGOAPIException(
                "影子复核发生并发冲突",
                "HIDDEN_REVIEW_CONFLICT", status_code=409,
            ) from None
        return _receipt(row)

    def summary(
        self, project_id: UUID, protocol_id: UUID,
    ) -> HiddenShadowReviewSummary:
        protocol = self._protocols.get(project_id, protocol_id)
        if protocol.finalized_at is None:
            raise TGOAPIException(
                "独立影子样本尚未冻结", "HIDDEN_REVIEW_NOT_READY",
                status_code=409,
            )
        cases = self._db.scalars(select(HiddenShadowProtocolCase).where(
            HiddenShadowProtocolCase.protocol_id == protocol_id,
            HiddenShadowProtocolCase.selected_position.is_not(None),
        )).all()
        if len(cases) != protocol.sample_count:
            self._evidence_changed()
        reviews = self._db.scalars(select(HiddenShadowCaseReview).where(
            HiddenShadowCaseReview.project_id == project_id,
            HiddenShadowCaseReview.protocol_id == protocol_id,
        )).all()
        by_candidate: dict[UUID, list[HiddenShadowCaseReview]] = {
            case.candidate_id: [] for case in cases
        }
        for review in reviews:
            if review.candidate_id not in by_candidate:
                self._evidence_changed()
            by_candidate[review.candidate_id].append(review)
        completed = sum(
            len(group) == 2 for group in by_candidate.values()
        )
        complete = bool(cases) and completed == len(cases)
        if not complete:
            return HiddenShadowReviewSummary(
                protocol_id=protocol_id, selected_cases=len(cases),
                completed_cases=completed, review_complete=False,
                severe_cases=None, disputed_cases=None,
                insufficient_cases=None,
            )
        severe = disputed = insufficient = 0
        for case in cases:
            detail = self._detail(case, project_id)
            pair = by_candidate[case.candidate_id]
            if (
                len(pair) != 2
                or pair[0].reviewer_id == pair[1].reviewer_id
                or any(row.evidence_sha256 != detail.evidence_sha256
                       for row in pair)
            ):
                self._evidence_changed()
            verdicts = {row.verdict for row in pair}
            severe += "severe_error" in verdicts
            disputed += len(verdicts) > 1
            insufficient += "insufficient_evidence" in verdicts
        return HiddenShadowReviewSummary(
            protocol_id=protocol_id, selected_cases=len(cases),
            completed_cases=completed, review_complete=True,
            severe_cases=severe, disputed_cases=disputed,
            insufficient_cases=insufficient,
        )

    @staticmethod
    def _evidence_changed() -> NoReturn:
        raise TGOAPIException(
            "冻结影子样本的证据已变化，停止复核并重新核查",
            "HIDDEN_REVIEW_EVIDENCE_CHANGED", status_code=409,
        )

    @staticmethod
    def _source_unavailable() -> NoReturn:
        raise TGOAPIException(
            "原消息服务暂不可用，无法核对案例来源",
            "HIDDEN_REVIEW_SOURCE_UNAVAILABLE", status_code=503,
        )
