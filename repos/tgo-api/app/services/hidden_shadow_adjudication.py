"""Resolve independent G4 review disputes without rewriting reviews."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.hidden_shadow_adjudication import HiddenShadowAdjudication
from app.models.hidden_shadow_protocol import HiddenShadowProtocolCase
from app.models.hidden_shadow_review import HiddenShadowCaseReview
from app.models.staff_message_delivery import StaffMessageDelivery
from app.schemas.hidden_shadow_adjudication import (
    HiddenShadowAdjudicationReceipt, HiddenShadowAdjudicationRequest,
    HiddenShadowReviewPair,
)
from app.schemas.hidden_shadow_review import HiddenShadowReviewReceipt
from app.services.hidden_shadow_protocol import HiddenShadowProtocolService
from app.services.hidden_shadow_review import HiddenShadowReviewService


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _review_set_digest(rows: Sequence[HiddenShadowCaseReview]) -> str:
    payload = [
        {
            "id": str(row.id),
            "reviewer_id": str(row.reviewer_id),
            "verdict": row.verdict,
            "reason_code": row.reason_code,
            "note": row.note,
            "evidence_sha256": row.evidence_sha256,
            "source_verified_at": (
                _utc(row.source_verified_at).isoformat()
                if row.source_verified_at is not None else None
            ),
            "source_message_seq": row.source_message_seq,
            "source_content_sha256": row.source_content_sha256,
            "created_at": _utc(row.created_at).isoformat(),
        }
        for row in sorted(rows, key=lambda item: item.reviewer_id.bytes)
    ]
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _request_fingerprint(
    protocol_id: UUID, candidate_id: UUID,
    request: HiddenShadowAdjudicationRequest,
) -> str:
    encoded = json.dumps({
        "protocol_id": str(protocol_id),
        "candidate_id": str(candidate_id),
        "expected_review_sha256": request.expected_review_sha256,
        "verdict": request.verdict,
        "reason_code": request.reason_code,
        "note": request.note,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _receipt(
    row: HiddenShadowAdjudication,
) -> HiddenShadowAdjudicationReceipt:
    return HiddenShadowAdjudicationReceipt.model_validate(
        row, from_attributes=True,
    )


class HiddenShadowAdjudicationService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._reviews = HiddenShadowReviewService(db)
        self._protocols = HiddenShadowProtocolService(db)

    def review_pair(
        self, project_id: UUID, protocol_id: UUID, candidate_id: UUID,
    ) -> HiddenShadowReviewPair:
        summary = self._reviews.summary(project_id, protocol_id)
        if not summary.review_complete:
            raise TGOAPIException(
                "请先完成本批全部样本的两人独立复核",
                "HIDDEN_ADJUDICATION_REVIEW_INCOMPLETE", status_code=409,
            )
        detail = self._reviews.get_case(
            project_id, protocol_id, candidate_id,
        )
        rows = self._db.scalars(select(HiddenShadowCaseReview).where(
            HiddenShadowCaseReview.project_id == project_id,
            HiddenShadowCaseReview.protocol_id == protocol_id,
            HiddenShadowCaseReview.candidate_id == candidate_id,
        )).all()
        if (
            len(rows) != 2
            or rows[0].reviewer_id == rows[1].reviewer_id
            or any(row.evidence_sha256 != detail.evidence_sha256
                   for row in rows)
        ):
            raise TGOAPIException(
                "样本复核证据不一致",
                "HIDDEN_ADJUDICATION_EVIDENCE_CHANGED", status_code=409,
            )
        digest = _review_set_digest(rows)
        adjudication = self._db.scalar(select(HiddenShadowAdjudication).where(
            HiddenShadowAdjudication.project_id == project_id,
            HiddenShadowAdjudication.protocol_id == protocol_id,
            HiddenShadowAdjudication.candidate_id == candidate_id,
        ))
        if adjudication is not None and (
            adjudication.review_set_sha256 != digest
            or adjudication.evidence_sha256 != detail.evidence_sha256
        ):
            raise TGOAPIException(
                "裁决依据已变化，请重新核对样本",
                "HIDDEN_ADJUDICATION_EVIDENCE_CHANGED", status_code=409,
            )
        ordered = sorted(rows, key=lambda row: row.reviewer_id.bytes)
        reviews = tuple(
            HiddenShadowReviewReceipt.model_validate(
                row, from_attributes=True,
            ) for row in ordered
        )
        return HiddenShadowReviewPair(
            protocol_id=protocol_id,
            candidate_id=candidate_id,
            visitor_id=detail.visitor_id,
            evidence_sha256=detail.evidence_sha256,
            review_set_sha256=digest,
            reviews=(reviews[0], reviews[1]),
            adjudication=(
                _receipt(adjudication) if adjudication is not None else None
            ),
        )

    def submit(
        self, project_id: UUID, protocol_id: UUID, candidate_id: UUID,
        adjudicator_id: UUID, request: HiddenShadowAdjudicationRequest,
    ) -> HiddenShadowAdjudicationReceipt:
        self._protocols.get(project_id, protocol_id)
        fingerprint = _request_fingerprint(
            protocol_id, candidate_id, request,
        )

        def prior() -> HiddenShadowAdjudication | None:
            existing: HiddenShadowAdjudication | None = self._db.scalar(
                select(HiddenShadowAdjudication).where(
                    HiddenShadowAdjudication.project_id == project_id,
                    HiddenShadowAdjudication.request_id == request.request_id,
                )
            )
            if existing is not None and (
                existing.adjudicator_id != adjudicator_id
                or existing.request_fingerprint != fingerprint
            ):
                raise TGOAPIException(
                    "裁决请求编号已用于其他结论",
                    "HIDDEN_ADJUDICATION_REQUEST_CONFLICT",
                    status_code=409,
                )
            return existing

        existing = prior()
        if existing is not None:
            return _receipt(existing)
        case = self._db.scalar(
            select(HiddenShadowProtocolCase).where(
                HiddenShadowProtocolCase.protocol_id == protocol_id,
                HiddenShadowProtocolCase.candidate_id == candidate_id,
                HiddenShadowProtocolCase.selected_position.is_not(None),
            ).with_for_update()
        )
        if case is None:
            raise TGOAPIException(
                "裁决案例不属于当前固定样本",
                "HIDDEN_ADJUDICATION_CASE_NOT_FOUND", status_code=404,
            )
        pair = self.review_pair(project_id, protocol_id, candidate_id)
        delivery = self._db.scalar(select(StaffMessageDelivery).where(
            StaffMessageDelivery.id == case.staff_delivery_id,
            StaffMessageDelivery.project_id == project_id,
        ))
        if delivery is None:
            raise TGOAPIException(
                "原人工回复证据缺失",
                "HIDDEN_ADJUDICATION_EVIDENCE_CHANGED", status_code=409,
            )
        if (
            adjudicator_id == delivery.staff_id
            or any(row.reviewer_id == adjudicator_id
                   for row in pair.reviews)
        ):
            raise TGOAPIException(
                "原客服或复核员不能裁决自己的案例",
                "HIDDEN_ADJUDICATION_SELF_REVIEW", status_code=403,
            )
        verdicts = {row.verdict for row in pair.reviews}
        if len(verdicts) != 2 or "insufficient_evidence" in verdicts:
            raise TGOAPIException(
                "当前案例没有可裁决的两人结论冲突",
                "HIDDEN_ADJUDICATION_NOT_DISPUTED", status_code=409,
            )
        if pair.review_set_sha256 != request.expected_review_sha256:
            raise TGOAPIException(
                "复核依据已变化，请刷新后重新裁决",
                "HIDDEN_ADJUDICATION_STALE", status_code=409,
            )
        if pair.adjudication is not None:
            raise TGOAPIException(
                "此案例已有不可修改的裁决",
                "HIDDEN_ADJUDICATION_ALREADY_DECIDED", status_code=409,
            )
        row = HiddenShadowAdjudication(
            project_id=project_id, protocol_id=protocol_id,
            candidate_id=candidate_id, adjudicator_id=adjudicator_id,
            request_id=request.request_id, request_fingerprint=fingerprint,
            review_set_sha256=pair.review_set_sha256,
            evidence_sha256=pair.evidence_sha256,
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
                "裁决发生并发冲突",
                "HIDDEN_ADJUDICATION_CONFLICT", status_code=409,
            ) from None
        return _receipt(row)
