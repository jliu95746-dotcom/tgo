"""Persist human signoff for independently frozen WeCom G4 evidence."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.hidden_shadow_gate_signoff import HiddenShadowGateSignoff
from app.models.hidden_shadow_protocol import HiddenShadowProtocol
from app.schemas.hidden_shadow_gate_signoff import (
    HiddenShadowGateSignoffReceipt,
)
from app.schemas.shadow_gate_signoff import (
    ShadowGateDecisionRequest, ShadowGateSignoffRequest,
)
from app.services.hidden_shadow_gate_assessment import (
    HiddenShadowGateAssessmentService,
)


def _fingerprint(
    request: ShadowGateSignoffRequest | ShadowGateDecisionRequest,
) -> str:
    return hashlib.sha256(request.model_dump_json(
        exclude={"request_id"},
    ).encode()).hexdigest()


class HiddenShadowGateSignoffService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._assessment = HiddenShadowGateAssessmentService(db)

    def _receipt(
        self, row: HiddenShadowGateSignoff,
    ) -> HiddenShadowGateSignoffReceipt:
        current_evidence_valid: bool | None = None
        if row.decision == "accepted":
            try:
                assessment = self._assessment.assess(
                    row.project_id, row.protocol_id,
                )
            except TGOAPIException:
                current_evidence_valid = False
            else:
                protocol = self._db.scalar(select(HiddenShadowProtocol).where(
                    HiddenShadowProtocol.id == row.protocol_id,
                    HiddenShadowProtocol.project_id == row.project_id,
                ))
                current_evidence_valid = bool(
                    protocol is not None
                    and assessment.can_request_g4_signoff
                    and assessment.sample_count == row.sample_count
                    and protocol.manifest_sha256 == row.manifest_sha256
                    and assessment.review_evidence_sha256
                    == row.review_evidence_sha256
                    and row.live_case_count == row.sample_count
                    and row.decider_id is not None
                    and row.decider_id != row.requester_id
                )
        return HiddenShadowGateSignoffReceipt.model_validate(
            row, from_attributes=True,
        ).model_copy(update={"current_evidence_valid": current_evidence_valid})

    def _row(
        self, project_id: UUID, protocol_id: UUID, *, lock: bool = False,
    ) -> HiddenShadowGateSignoff | None:
        query = select(HiddenShadowGateSignoff).where(
            HiddenShadowGateSignoff.project_id == project_id,
            HiddenShadowGateSignoff.protocol_id == protocol_id,
        )
        if lock:
            query = query.with_for_update()
        return self._db.scalar(query)

    def get(
        self, project_id: UUID, protocol_id: UUID,
    ) -> HiddenShadowGateSignoffReceipt | None:
        row = self._row(project_id, protocol_id)
        return self._receipt(row) if row is not None else None

    def _require_ready(
        self, project_id: UUID, protocol_id: UUID,
    ) -> tuple[int, str, str]:
        assessment = self._assessment.assess(project_id, protocol_id)
        protocol = self._db.scalar(select(HiddenShadowProtocol).where(
            HiddenShadowProtocol.id == protocol_id,
            HiddenShadowProtocol.project_id == project_id,
        ))
        if (
            protocol is None
            or not assessment.can_request_g4_signoff
            or assessment.sample_count is None
            or assessment.sample_count < 1
            or protocol.manifest_sha256 is None
            or assessment.review_evidence_sha256 is None
        ):
            raise TGOAPIException(
                "独立影子样本尚未满足人工签收条件",
                "HIDDEN_GATE_NOT_READY", status_code=409,
            )
        return (
            assessment.sample_count, protocol.manifest_sha256,
            assessment.review_evidence_sha256,
        )

    def request(
        self, project_id: UUID, protocol_id: UUID, requester_id: UUID,
        request: ShadowGateSignoffRequest,
    ) -> HiddenShadowGateSignoffReceipt:
        fingerprint = _fingerprint(request)

        def prior() -> HiddenShadowGateSignoffReceipt | None:
            row = self._row(project_id, protocol_id)
            if row is None:
                return None
            if (
                row.request_id != request.request_id
                or row.request_fingerprint != fingerprint
                or row.requester_id != requester_id
            ):
                raise TGOAPIException(
                    "此独立影子验收方案已有签收申请",
                    "HIDDEN_GATE_ALREADY_REQUESTED", status_code=409,
                )
            return self._receipt(row)

        existing = prior()
        if existing is not None:
            return existing
        protocol = self._db.scalar(select(HiddenShadowProtocol).where(
            HiddenShadowProtocol.project_id == project_id,
            HiddenShadowProtocol.id == protocol_id,
        ).with_for_update())
        if protocol is None:
            raise TGOAPIException(
                "独立影子验收方案不存在",
                "HIDDEN_PROTOCOL_NOT_FOUND", status_code=404,
            )
        sample_count, manifest_sha, review_sha = self._require_ready(
            project_id, protocol_id,
        )
        if request.live_case_count != sample_count:
            raise TGOAPIException(
                "真实渠道核对数量必须覆盖全部抽中案例",
                "HIDDEN_GATE_LIVE_COUNT_MISMATCH", status_code=409,
            )
        row = HiddenShadowGateSignoff(
            project_id=project_id, protocol_id=protocol_id,
            requester_id=requester_id, request_id=request.request_id,
            request_fingerprint=fingerprint,
            manifest_sha256=manifest_sha,
            review_evidence_sha256=review_sha,
            sample_count=sample_count,
            evidence_reference=request.evidence_reference,
            live_case_count=request.live_case_count, note=request.note,
            created_at=datetime.now(timezone.utc),
        )
        self._db.add(row)
        try:
            self._db.commit()
        except IntegrityError:
            self._db.rollback()
            existing = prior()
            if existing is not None:
                return existing
            raise TGOAPIException(
                "独立影子签收申请发生并发冲突",
                "HIDDEN_GATE_CONFLICT", status_code=409,
            ) from None
        return self._receipt(row)

    def decide(
        self, project_id: UUID, protocol_id: UUID, decider_id: UUID,
        request: ShadowGateDecisionRequest,
    ) -> HiddenShadowGateSignoffReceipt:
        row = self._row(project_id, protocol_id, lock=True)
        if row is None:
            raise TGOAPIException(
                "独立影子签收申请不存在",
                "HIDDEN_GATE_REQUEST_NOT_FOUND", status_code=404,
            )
        fingerprint = _fingerprint(request)
        if row.decision is not None:
            if (
                row.decider_id == decider_id
                and row.decision_request_id == request.request_id
                and row.decision_fingerprint == fingerprint
            ):
                return self._receipt(row)
            raise TGOAPIException(
                "独立影子签收已有不可修改的结论",
                "HIDDEN_GATE_ALREADY_DECIDED", status_code=409,
            )
        if row.requester_id == decider_id:
            raise TGOAPIException(
                "申请人不能签收自己的验收申请",
                "HIDDEN_GATE_SELF_SIGNOFF", status_code=403,
            )
        if request.decision == "accepted":
            if not request.live_evidence_verified:
                raise TGOAPIException(
                    "批准前须由独立管理员核对企业微信原消息和回执",
                    "HIDDEN_GATE_LIVE_EVIDENCE_REQUIRED", status_code=409,
                )
            count, manifest_sha, review_sha = self._require_ready(
                project_id, protocol_id,
            )
            if (
                count != row.sample_count
                or manifest_sha != row.manifest_sha256
                or review_sha != row.review_evidence_sha256
            ):
                raise TGOAPIException(
                    "签收申请后的影子证据已变化，请重新验收",
                    "HIDDEN_GATE_EVIDENCE_CHANGED", status_code=409,
                )
        row.decision = request.decision
        row.decider_id = decider_id
        row.decision_request_id = request.request_id
        row.decision_fingerprint = fingerprint
        row.decision_note = request.note
        row.decided_at = datetime.now(timezone.utc)
        self._db.commit()
        return self._receipt(row)
