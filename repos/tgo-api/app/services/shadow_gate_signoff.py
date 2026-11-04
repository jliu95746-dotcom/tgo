"""Persist a two-person G4 decision without changing customer reply routing."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.shadow_gate_signoff import ShadowGateSignoff
from app.models.shadow_review_protocol import ShadowReviewProtocol
from app.schemas.shadow_gate_signoff import (
    ShadowGateDecisionRequest,
    ShadowGateSignoffReceipt,
    ShadowGateSignoffRequest,
)
from app.services.shadow_gate_assessment import ShadowGateAssessmentService
from app.services.shadow_adjudication_service import current_review_snapshot
from app.services.shadow_sample_service import ShadowSampleService
from app.services.shadow_sample_selection import WECOM_SAMPLE_ALGORITHM


def _receipt(row: ShadowGateSignoff) -> ShadowGateSignoffReceipt:
    return ShadowGateSignoffReceipt.model_validate(row, from_attributes=True)


def _fingerprint(request: ShadowGateSignoffRequest | ShadowGateDecisionRequest) -> str:
    return hashlib.sha256(
        request.model_dump_json(exclude={"request_id"}).encode("utf-8")
    ).hexdigest()


class ShadowGateSignoffService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def _row(self, project_id: UUID, protocol_id: UUID, *, lock: bool = False) -> ShadowGateSignoff | None:
        query = select(ShadowGateSignoff).where(
            ShadowGateSignoff.project_id == project_id,
            ShadowGateSignoff.protocol_id == protocol_id,
        )
        if lock:
            query = query.with_for_update()
        return self._db.scalar(query)

    def get(self, project_id: UUID, protocol_id: UUID) -> ShadowGateSignoffReceipt | None:
        row = self._row(project_id, protocol_id)
        return _receipt(row) if row is not None else None

    def _current_review_digest(
        self, project_id: UUID, batch_id: UUID, expected_count: int,
    ) -> str:
        batch = ShadowSampleService(self._db).get_batch(project_id, batch_id)
        if len(batch.items) != expected_count:
            raise TGOAPIException(
                "固定样本数量与复核证据不一致",
                "SHADOW_GATE_NOT_READY", status_code=409,
            )
        digest = hashlib.sha256()
        for item in sorted(batch.items, key=lambda member: member.position):
            snapshot = current_review_snapshot(self._db, project_id, item.draft_id)
            if (
                item.review_status not in {"agreed", "adjudicated"}
                or item.reviewer_count < 2
                or (
                    item.review_set_sha256 is not None
                    and item.review_set_sha256 != snapshot.sha256
                )
            ):
                raise TGOAPIException(
                    "样本复核依据不完整",
                    "SHADOW_GATE_NOT_READY", status_code=409,
                )
            digest.update(item.draft_id.bytes)
            digest.update(snapshot.sha256.encode("ascii"))
            digest.update(item.review_status.encode("ascii"))
            digest.update((item.adjudication_verdict or "").encode("ascii"))
            digest.update(b"\0")
        return digest.hexdigest()

    def _require_ready(self, project_id: UUID, protocol: ShadowReviewProtocol) -> tuple[UUID, int, str]:
        if protocol.algorithm != WECOM_SAMPLE_ALGORITHM:
            raise TGOAPIException(
                "此验收方案未按企业微信客服渠道抽样，须重新预登记",
                "SHADOW_GATE_OFFICIAL_CHANNEL_REQUIRED", status_code=409,
            )
        assessment = ShadowGateAssessmentService(self._db).assess(
            project_id, protocol.id
        )
        if (
            not assessment.can_request_g4_signoff
            or assessment.batch_id is None
            or assessment.sample_count is None
            or assessment.sample_count < 1
            or protocol.manifest_sha256 is None
        ):
            raise TGOAPIException(
                "正式样本尚未满足人工签收条件",
                "SHADOW_GATE_NOT_READY", status_code=409,
            )
        return (
            assessment.batch_id,
            assessment.sample_count,
            self._current_review_digest(
                project_id, assessment.batch_id, assessment.sample_count
            ),
        )

    def request(
        self, project_id: UUID, protocol_id: UUID, requester_id: UUID,
        request: ShadowGateSignoffRequest,
    ) -> ShadowGateSignoffReceipt:
        fingerprint = _fingerprint(request)

        def existing_receipt() -> ShadowGateSignoffReceipt | None:
            existing = self._row(project_id, protocol_id)
            if existing is None:
                return None
            if (
                existing.request_id != request.request_id
                or existing.request_fingerprint != fingerprint
                or existing.requester_id != requester_id
            ):
                raise TGOAPIException(
                    "该正式影子验收方案已提交签收申请",
                    "SHADOW_GATE_ALREADY_REQUESTED", status_code=409,
                )
            return _receipt(existing)

        prior = existing_receipt()
        if prior is not None:
            return prior
        protocol = self._db.scalar(
            select(ShadowReviewProtocol).where(
                ShadowReviewProtocol.project_id == project_id,
                ShadowReviewProtocol.id == protocol_id,
            ).with_for_update()
        )
        if protocol is None:
            raise TGOAPIException(
                "正式影子验收方案不存在",
                "SHADOW_PROTOCOL_NOT_FOUND", status_code=404,
            )
        batch_id, sample_count, review_digest = self._require_ready(
            project_id, protocol
        )
        row = ShadowGateSignoff(
            project_id=project_id, protocol_id=protocol_id, batch_id=batch_id,
            requester_id=requester_id, request_id=request.request_id,
            request_fingerprint=fingerprint,
            manifest_sha256=protocol.manifest_sha256,
            review_evidence_sha256=review_digest,
            sample_count=sample_count,
            evidence_reference=request.evidence_reference,
            live_case_count=request.live_case_count, note=request.note,
            created_at=datetime.now(timezone.utc),
        )
        self._db.add(row)
        try:
            self._db.commit()
            return _receipt(row)
        except IntegrityError:
            self._db.rollback()
            prior = existing_receipt()
            if prior is None:
                raise
            return prior

    def decide(
        self, project_id: UUID, protocol_id: UUID, decider_id: UUID,
        request: ShadowGateDecisionRequest,
    ) -> ShadowGateSignoffReceipt:
        row = self._row(project_id, protocol_id, lock=True)
        if row is None:
            raise TGOAPIException(
                "人工签收申请不存在",
                "SHADOW_GATE_REQUEST_NOT_FOUND", status_code=404,
            )
        fingerprint = _fingerprint(request)
        if row.decision is not None:
            if (
                row.decider_id == decider_id
                and row.decision_request_id == request.request_id
                and row.decision_fingerprint == fingerprint
            ):
                return _receipt(row)
            raise TGOAPIException(
                "人工签收已有不可修改的结论",
                "SHADOW_GATE_ALREADY_DECIDED", status_code=409,
            )
        if row.requester_id == decider_id:
            raise TGOAPIException(
                "申请人不能签收自己的验收申请",
                "SHADOW_GATE_SELF_SIGNOFF", status_code=403,
            )
        if request.decision == "accepted":
            if not request.live_evidence_verified:
                raise TGOAPIException(
                    "签收前须由独立管理员核对真实渠道证据",
                    "SHADOW_GATE_LIVE_EVIDENCE_REQUIRED", status_code=409,
                )
            protocol = self._db.scalar(select(ShadowReviewProtocol).where(
                ShadowReviewProtocol.project_id == project_id,
                ShadowReviewProtocol.id == protocol_id,
            ))
            if protocol is None:
                raise TGOAPIException(
                    "正式影子验收方案不存在",
                    "SHADOW_PROTOCOL_NOT_FOUND", status_code=404,
                )
            batch_id, count, digest = self._require_ready(project_id, protocol)
            if (
                batch_id != row.batch_id or count != row.sample_count
                or digest != row.review_evidence_sha256
                or protocol.manifest_sha256 != row.manifest_sha256
            ):
                raise TGOAPIException(
                    "申请后样本证据已变化，请重新开展正式验收",
                    "SHADOW_GATE_EVIDENCE_CHANGED", status_code=409,
                )
        row.decision = request.decision
        row.decider_id = decider_id
        row.decision_request_id = request.request_id
        row.decision_fingerprint = fingerprint
        row.decision_note = request.note
        row.decided_at = datetime.now(timezone.utc)
        self._db.commit()
        return _receipt(row)
