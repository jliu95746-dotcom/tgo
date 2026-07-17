"""Platform-authenticated message analysis persistence endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.message_analysis import (
    CombinedMessageAnalysisResponse,
    IntentResultResponse,
    IntentResultUpsertRequest,
    MediaResultResponse,
    MediaResultUpsertRequest,
)
from app.services.message_analysis_service import MessageAnalysisService


router = APIRouter()

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
