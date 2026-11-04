"""Platform-write and staff-read message analysis endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Response
from sqlalchemy.orm import Session
from uuid import UUID

from app.core.database import get_db
from app.core.security import require_admin, require_permission
from app.models import Staff
from app.schemas.message_analysis import (
    CombinedMessageAnalysisResponse,
    IntentResultResponse,
    IntentResultUpsertRequest,
    MediaResultResponse,
    MediaResultUpsertRequest,
    StaffMessageAnalysisBatchRequest,
    StaffMessageAnalysisBatchResponse,
    StaffMessageAnalysisResponse,
)
from app.services.message_analysis_service import (
    MessageAnalysisLookupKey,
    MessageAnalysisService,
)
from app.schemas.shadow_review import (
    ShadowComparisonsResponse,
    ShadowPendingReviewQueue,
    ShadowReviewReceipt,
    ShadowReviewRequest,
    ShadowReviewSummary,
)
from app.schemas.shadow_adjudication import (
    ShadowAdjudicationHistory,
    ShadowAdjudicationReceipt,
    ShadowAdjudicationRequest,
)
from app.schemas.intent_evaluation_export import IntentEvaluationExportRequest
from app.services.intent_evaluation_export import IntentEvaluationExportService
from app.services.shadow_review_audit import ShadowReviewAudit
from app.services.shadow_blind_review_access import ShadowBlindReviewAccess
from app.services.shadow_adjudication_service import ShadowAdjudicationService
from app.services.shadow_review_service import ShadowReviewService
from app.schemas.shadow_sample import (
    ShadowProtocolCreateRequest,
    ShadowProtocolCandidatePage,
    ShadowProtocolIntegrityResponse,
    ShadowProtocolList,
    ShadowProtocolResponse,
    ShadowSampleBatchList,
    ShadowSampleBatchResponse,
    ShadowSampleCreateRequest,
)
from app.services.shadow_sample_service import ShadowSampleService
from app.services.shadow_review_protocol_service import ShadowReviewProtocolService
from app.schemas.shadow_gate_assessment import ShadowGateAssessment
from app.services.shadow_gate_assessment import ShadowGateAssessmentService
from app.schemas.shadow_gate_signoff import (
    ShadowGateDecisionRequest,
    ShadowGateSignoffReceipt,
    ShadowGateSignoffRequest,
)
from app.services.shadow_gate_signoff import ShadowGateSignoffService
from app.utils.encoding import (
    build_visitor_channel_id,
    parse_visitor_channel_id,
)


router = APIRouter()
require_message_analysis_read = require_permission("visitors:read")


@router.post(
    "/staff/intent-evaluation-predictions",
    summary="Export selected project-owned intent results as deidentified G3 JSONL",
)
def export_staff_intent_evaluation_predictions(
    request: IntentEvaluationExportRequest,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> Response:
    content = IntentEvaluationExportService(db).export(current_user.project_id, request)
    return Response(
        content=content,
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="intent-g3-predictions.jsonl"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/staff/shadow-cases",
    response_model=ShadowComparisonsResponse,
    summary="Review shadow intent analyses against confirmed staff replies",
)
def get_staff_shadow_cases(
    visitor_id: UUID,
    response: Response,
    limit: int = Query(default=30, ge=1, le=100),
    focus_draft_id: UUID | None = None,
    current_user: Staff = Depends(require_message_analysis_read),
    db: Session = Depends(get_db),
) -> ShadowComparisonsResponse:
    from app.services.staff_conversation_scope import require_owned_visitor

    require_owned_visitor(db, current_user, visitor_id)
    response.headers["Cache-Control"] = "no-store"
    return ShadowReviewService(db).list_comparisons(
        current_user.project_id,
        visitor_id,
        current_user.id,
        limit,
        focus_draft_id=focus_draft_id,
    )


@router.post(
    "/staff/shadow-reviews",
    response_model=ShadowReviewReceipt,
    summary="Record an administrator's shadow draft review",
)
def submit_staff_shadow_review(
    request: ShadowReviewRequest,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ShadowReviewReceipt:
    from app.services.staff_conversation_scope import require_owned_visitor

    require_owned_visitor(db, current_user, request.visitor_id)
    response.headers["Cache-Control"] = "no-store"
    return ShadowReviewAudit(db).submit(
        current_user.project_id, current_user.id, request
    )


@router.post(
    "/staff/shadow-adjudications",
    response_model=ShadowAdjudicationReceipt,
    summary="Resolve one frozen shadow sample's disputed review snapshot",
)
def submit_staff_shadow_adjudication(
    request: ShadowAdjudicationRequest,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ShadowAdjudicationReceipt:
    from app.services.staff_conversation_scope import require_owned_visitor

    require_owned_visitor(db, current_user, request.visitor_id)
    response.headers["Cache-Control"] = "no-store"
    return ShadowAdjudicationService(db).submit(
        current_user.project_id, current_user.id, request
    )


@router.get(
    "/staff/shadow-adjudications",
    response_model=ShadowAdjudicationHistory,
    summary="Read append-only decisions for a project-owned frozen shadow sample",
)
def list_staff_shadow_adjudications(
    batch_id: UUID,
    draft_id: UUID,
    visitor_id: UUID,
    response: Response,
    limit: int = Query(default=20, ge=1, le=100),
    before_id: UUID | None = None,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ShadowAdjudicationHistory:
    from app.services.staff_conversation_scope import require_owned_visitor

    require_owned_visitor(db, current_user, visitor_id)
    response.headers["Cache-Control"] = "no-store"
    ShadowBlindReviewAccess(db).require_result_access(
        current_user.project_id, current_user.id
    )
    return ShadowAdjudicationService(db).list_history(
        current_user.project_id, batch_id, draft_id, visitor_id,
        limit=limit, before_id=before_id,
    )


@router.get(
    "/staff/shadow-review-summary",
    response_model=ShadowReviewSummary,
    summary=(
        "Summarize candidate shadow review evidence without a pass decision"
    ),
)
def get_staff_shadow_review_summary(
    response: Response,
    days: int = Query(default=7, ge=1, le=90),
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ShadowReviewSummary:
    response.headers["Cache-Control"] = "no-store"
    ShadowBlindReviewAccess(db).require_result_access(
        current_user.project_id, current_user.id
    )
    return ShadowReviewAudit(db).summary(current_user.project_id, days=days)


@router.get(
    "/staff/shadow-review-queue",
    response_model=ShadowPendingReviewQueue,
    summary="Locate verified shadow drafts awaiting administrator review",
)
def get_staff_shadow_review_queue(
    response: Response,
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=10, ge=1, le=50),
    after_draft_id: UUID | None = None,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ShadowPendingReviewQueue:
    response.headers["Cache-Control"] = "no-store"
    return ShadowReviewAudit(db).pending_queue(
        current_user.project_id,
        current_user.id,
        days=days,
        limit=limit,
        after_draft_id=after_draft_id,
    )


@router.post(
    "/staff/shadow-review-protocol",
    response_model=ShadowProtocolResponse,
    status_code=201,
    summary="Pre-register the project's official shadow review window",
)
def register_staff_shadow_review_protocol(
    request: ShadowProtocolCreateRequest,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ShadowProtocolResponse:
    response.headers["Cache-Control"] = "no-store"
    return ShadowReviewProtocolService(db).register(
        current_user.project_id, current_user.id, request
    )


@router.get(
    "/staff/shadow-review-protocol",
    response_model=ShadowProtocolResponse | None,
    summary="Read the project's official shadow review protocol",
)
def get_staff_shadow_review_protocol(
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ShadowProtocolResponse | None:
    response.headers["Cache-Control"] = "no-store"
    return ShadowReviewProtocolService(db).get(current_user.project_id)


@router.get(
    "/staff/shadow-review-protocols/{protocol_id}/candidates",
    response_model=ShadowProtocolCandidatePage,
    summary="Page through the frozen official G4 candidate manifest",
)
def list_staff_shadow_review_protocol_candidates(
    protocol_id: UUID,
    response: Response,
    limit: int = Query(default=100, ge=1, le=200),
    after_draft_id: UUID | None = None,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ShadowProtocolCandidatePage:
    response.headers["Cache-Control"] = "no-store"
    return ShadowReviewProtocolService(db).list_candidates(
        current_user.project_id,
        protocol_id,
        limit=limit,
        after_draft_id=after_draft_id,
    )


@router.get(
    "/staff/shadow-review-protocols/{protocol_id}/integrity",
    response_model=ShadowProtocolIntegrityResponse,
    summary="Recalculate the frozen official G4 manifest and selected membership",
)
def verify_staff_shadow_review_protocol_integrity(
    protocol_id: UUID,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ShadowProtocolIntegrityResponse:
    response.headers["Cache-Control"] = "no-store"
    return ShadowReviewProtocolService(db).verify_integrity(
        current_user.project_id, protocol_id
    )


@router.get(
    "/staff/shadow-review-protocols/{protocol_id}/readiness",
    response_model=ShadowGateAssessment,
    summary="Assess official G4 shadow review evidence before manual signoff",
)
def get_staff_shadow_gate_readiness(
    protocol_id: UUID,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ShadowGateAssessment:
    response.headers["Cache-Control"] = "no-store"
    ShadowBlindReviewAccess(db).require_result_access(
        current_user.project_id, current_user.id
    )
    return ShadowGateAssessmentService(db).assess(
        current_user.project_id, protocol_id
    )


@router.get(
    "/staff/shadow-review-protocols/{protocol_id}/signoff",
    response_model=ShadowGateSignoffReceipt | None,
    summary="Read the recorded human G4 signoff request and decision",
)
def get_staff_shadow_gate_signoff(
    protocol_id: UUID,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ShadowGateSignoffReceipt | None:
    response.headers["Cache-Control"] = "no-store"
    ShadowBlindReviewAccess(db).require_result_access(
        current_user.project_id, current_user.id
    )
    return ShadowGateSignoffService(db).get(current_user.project_id, protocol_id)


@router.post(
    "/staff/shadow-review-protocols/{protocol_id}/signoff",
    response_model=ShadowGateSignoffReceipt,
    status_code=201,
    summary="Request human G4 signoff against fixed reviewed evidence",
)
def request_staff_shadow_gate_signoff(
    protocol_id: UUID,
    request: ShadowGateSignoffRequest,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ShadowGateSignoffReceipt:
    response.headers["Cache-Control"] = "no-store"
    ShadowBlindReviewAccess(db).require_result_access(
        current_user.project_id, current_user.id
    )
    return ShadowGateSignoffService(db).request(
        current_user.project_id, protocol_id, current_user.id, request
    )


@router.post(
    "/staff/shadow-review-protocols/{protocol_id}/signoff/decision",
    response_model=ShadowGateSignoffReceipt,
    summary="Record an independent human G4 decision without enabling replies",
)
def decide_staff_shadow_gate_signoff(
    protocol_id: UUID,
    request: ShadowGateDecisionRequest,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ShadowGateSignoffReceipt:
    response.headers["Cache-Control"] = "no-store"
    ShadowBlindReviewAccess(db).require_result_access(
        current_user.project_id, current_user.id
    )
    return ShadowGateSignoffService(db).decide(
        current_user.project_id, protocol_id, current_user.id, request
    )


@router.get(
    "/staff/shadow-review-protocols",
    response_model=ShadowProtocolList,
    summary="List all pre-registered official shadow review cycles",
)
def list_staff_shadow_review_protocols(
    response: Response,
    limit: int = Query(default=20, ge=1, le=100),
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ShadowProtocolList:
    response.headers["Cache-Control"] = "no-store"
    return ShadowReviewProtocolService(db).list(current_user.project_id, limit=limit)


@router.post(
    "/staff/shadow-review-protocol/finalize",
    response_model=ShadowProtocolResponse,
    summary="Freeze the pre-registered official shadow review sample",
)
def finalize_staff_shadow_review_protocol(
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ShadowProtocolResponse:
    response.headers["Cache-Control"] = "no-store"
    return ShadowReviewProtocolService(db).finalize(current_user.project_id)


@router.post(
    "/staff/shadow-sample-batches",
    response_model=ShadowSampleBatchResponse,
    status_code=201,
    summary="Freeze an administrator's reproducible shadow review sample",
)
def create_staff_shadow_sample_batch(
    request: ShadowSampleCreateRequest,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ShadowSampleBatchResponse:
    response.headers["Cache-Control"] = "no-store"
    ShadowBlindReviewAccess(db).require_result_access(
        current_user.project_id, current_user.id
    )
    return ShadowSampleService(db).create_batch(
        current_user.project_id, current_user.id, request
    )


@router.get(
    "/staff/shadow-sample-batches",
    response_model=ShadowSampleBatchList,
    summary="List the project's frozen shadow review samples",
)
def list_staff_shadow_sample_batches(
    response: Response,
    limit: int = Query(default=20, ge=1, le=100),
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ShadowSampleBatchList:
    response.headers["Cache-Control"] = "no-store"
    return ShadowSampleService(db).list_batches(
        current_user.project_id, limit=limit
    )


@router.get(
    "/staff/shadow-sample-batches/{batch_id}",
    response_model=ShadowSampleBatchResponse,
    summary="Read frozen shadow sample membership",
)
def get_staff_shadow_sample_batch(
    batch_id: UUID,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ShadowSampleBatchResponse:
    response.headers["Cache-Control"] = "no-store"
    ShadowBlindReviewAccess(db).require_result_access(
        current_user.project_id, current_user.id
    )
    return ShadowSampleService(db).get_batch(current_user.project_id, batch_id)


SourceMessageId = Annotated[
    str,
    Path(
        min_length=1,
        max_length=255,
        description="Source platform message ID",
    ),
]
PlatformAPIKey = Annotated[
    str | None,
    Header(alias="X-Platform-API-Key"),
]


@router.put(
    "/messages/{source_message_id}/media",
    response_model=MediaResultResponse,
    summary="Persist media analysis result",
)
def put_media_result(
    source_message_id: SourceMessageId,
    request: MediaResultUpsertRequest,
    x_platform_api_key: PlatformAPIKey = None,
    db: Session = Depends(get_db),
) -> MediaResultResponse:
    """Store one tenant-scoped media result idempotently."""
    result = MessageAnalysisService(db).upsert_media_result(
        platform_api_key=x_platform_api_key or "",
        source_message_id=source_message_id,
        request=request,
    )
    return MediaResultResponse.model_validate(result)


@router.put(
    "/messages/{source_message_id}/intent",
    response_model=IntentResultResponse,
    summary="Persist intent classification result",
)
def put_intent_result(
    source_message_id: SourceMessageId,
    request: IntentResultUpsertRequest,
    x_platform_api_key: PlatformAPIKey = None,
    db: Session = Depends(get_db),
) -> IntentResultResponse:
    """Store one tenant-scoped intent result idempotently."""
    result = MessageAnalysisService(db).upsert_intent_result(
        platform_api_key=x_platform_api_key or "",
        source_message_id=source_message_id,
        request=request,
    )
    return IntentResultResponse.model_validate(result)


@router.get(
    "/messages/{source_message_id}",
    response_model=CombinedMessageAnalysisResponse,
    summary="Get current message analysis",
)
def get_message_analysis(
    source_message_id: SourceMessageId,
    x_platform_api_key: PlatformAPIKey = None,
    db: Session = Depends(get_db),
) -> CombinedMessageAnalysisResponse:
    """Read media and intent projections within the authenticated platform."""
    combined = MessageAnalysisService(db).get_combined_result(
        platform_api_key=x_platform_api_key or "",
        source_message_id=source_message_id,
    )
    return CombinedMessageAnalysisResponse(
        source_message_id=source_message_id,
        media=(
            MediaResultResponse.model_validate(combined.media)
            if combined.media is not None
            else None
        ),
        intent=(
            IntentResultResponse.model_validate(combined.intent)
            if combined.intent is not None
            else None
        ),
    )


@router.post(
    "/staff/messages/query",
    response_model=StaffMessageAnalysisBatchResponse,
    summary="Get employee-visible message analyses",
)
def get_staff_message_analyses(
    request: StaffMessageAnalysisBatchRequest,
    current_user: Staff = Depends(require_message_analysis_read),
    db: Session = Depends(get_db),
) -> StaffMessageAnalysisBatchResponse:
    """Batch-read message analyses within the authenticated staff project."""
    key_to_channel = {
        MessageAnalysisLookupKey(
            visitor_id=parse_visitor_channel_id(message.channel_id),
            source_message_id=message.source_message_id,
        ): message.channel_id
        for message in request.messages
    }
    from app.services.staff_conversation_scope import require_owned_visitor
    for visitor_id in {key.visitor_id for key in key_to_channel}:
        require_owned_visitor(db, current_user, visitor_id)
    combined_results = MessageAnalysisService(
        db
    ).get_combined_results_for_project(
        project_id=current_user.project_id,
        keys=tuple(key_to_channel),
    )
    return StaffMessageAnalysisBatchResponse(
        items=tuple(
            StaffMessageAnalysisResponse(
                channel_id=key_to_channel.get(
                    combined.key,
                    build_visitor_channel_id(combined.key.visitor_id),
                ),
                source_message_id=combined.key.source_message_id,
                media=(
                    MediaResultResponse.model_validate(combined.media)
                    if combined.media is not None
                    else None
                ),
                intent=(
                    IntentResultResponse.model_validate(combined.intent)
                    if combined.intent is not None
                    else None
                ),
            )
            for combined in combined_results
        )
    )
