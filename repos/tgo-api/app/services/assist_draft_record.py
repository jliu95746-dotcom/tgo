"""Store staff-visible drafts and authorize a later human reply link."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.assist_draft_record import AssistDraftRecord
from app.schemas.chat import AssistDraftRequest, AssistDraftResponse
from app.schemas.shadow_review import ShadowSourceProof


def get_assist_draft(
    db: Session,
    *,
    project_id: UUID,
    staff_id: UUID,
    request_id: UUID,
    fingerprint: str,
) -> AssistDraftRecord | None:
    row: AssistDraftRecord | None = db.scalar(
        select(AssistDraftRecord).where(
            AssistDraftRecord.project_id == project_id,
            AssistDraftRecord.staff_id == staff_id,
            AssistDraftRecord.request_id == request_id,
        )
    )
    if row is not None and row.request_fingerprint != fingerprint:
        raise HTTPException(409, "同一草稿请求编号不能用于不同内容")
    return row


def response_from_record(row: AssistDraftRecord) -> AssistDraftResponse:
    if row.response_data.get("retention_redacted_at"):
        raise HTTPException(410, "AI 草稿正文已按保留期限清理")
    return AssistDraftResponse.model_validate(
        {**row.response_data, "draft_id": row.id}
    )


def save_assist_draft(
    db: Session,
    *,
    project_id: UUID,
    staff_id: UUID,
    request: AssistDraftRequest,
    fingerprint: str,
    response: AssistDraftResponse,
    source_proof: ShadowSourceProof | None = None,
) -> AssistDraftRecord:
    previous = get_assist_draft(
        db,
        project_id=project_id,
        staff_id=staff_id,
        request_id=request.request_id,
        fingerprint=fingerprint,
    )
    if previous is not None:
        return previous
    row = AssistDraftRecord(
        project_id=project_id,
        staff_id=staff_id,
        visitor_id=request.visitor_id,
        request_id=request.request_id,
        source_message_id=request.source_message_id,
        request_fingerprint=fingerprint,
        response_data=response.model_dump(mode="json", exclude={"draft_id"}),
        source_proof=source_proof.model_dump(mode="json")
        if source_proof
        else None,
    )
    db.add(row)
    try:
        db.commit()
        return row
    except IntegrityError:
        db.rollback()
        previous = get_assist_draft(
            db,
            project_id=project_id,
            staff_id=staff_id,
            request_id=request.request_id,
            fingerprint=fingerprint,
        )
        if previous is None:
            raise
        return previous


def resolve_delivery_draft(
    db: Session,
    *,
    draft_id: UUID,
    project_id: UUID,
    staff_id: UUID,
    visitor_id: UUID,
    source_message_id: str | None,
    payload_type: object,
) -> UUID:
    if not source_message_id or payload_type not in (1, 12):
        raise HTTPException(409, "AI 草稿与本次客户消息不匹配")
    row: AssistDraftRecord | None = db.scalar(
        select(AssistDraftRecord).where(
            AssistDraftRecord.id == draft_id,
            AssistDraftRecord.project_id == project_id,
            AssistDraftRecord.staff_id == staff_id,
            AssistDraftRecord.visitor_id == visitor_id,
            AssistDraftRecord.source_message_id == source_message_id,
        )
    )
    if row is None:
        raise HTTPException(409, "AI 草稿与本次客户消息不匹配")
    if row.response_data.get("retention_redacted_at"):
        raise HTTPException(409, "AI 草稿正文已按保留期限清理")
    return draft_id
