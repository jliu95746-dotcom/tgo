"""Platform-write and staff-read message analysis endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_permission
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
from app.utils.encoding import (
    build_visitor_channel_id,
    parse_visitor_channel_id,
)


router = APIRouter()
require_message_analysis_read = require_permission("visitors:read")

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
