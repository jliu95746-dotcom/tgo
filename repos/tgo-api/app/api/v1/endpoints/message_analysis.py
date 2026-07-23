"""Platform-authenticated message analysis persistence endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_authenticated_project
from app.models import Project
from app.schemas.message_analysis import (
    CombinedMessageAnalysisResponse,
    IntentResultResponse,
    IntentResultUpsertRequest,
    MediaResultResponse,
    MediaResultUpsertRequest,
    MessageAnalysisBatchRequest,
    MessageAnalysisBatchResponse,
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


@router.post(
    "/staff/messages/batch",
    response_model=MessageAnalysisBatchResponse,
    summary="Batch-read message analysis for the staff console",
)
def get_staff_message_analysis_batch(
    request: MessageAnalysisBatchRequest,
    authenticated: tuple[Project, str] = Depends(get_authenticated_project),
    db: Session = Depends(get_db),
) -> MessageAnalysisBatchResponse:
    """Return only analysis rows belonging to the signed-in staff project."""
    project, _ = authenticated
    combined = MessageAnalysisService(db).get_combined_results_for_project(
        project_id=project.id,
        source_message_ids=request.source_message_ids,
    )
    results: list[CombinedMessageAnalysisResponse] = []
    for source_message_id in request.source_message_ids:
        item = combined.get(source_message_id)
        if item is None:
            continue
        results.append(
            CombinedMessageAnalysisResponse(
                source_message_id=source_message_id,
                media=(
                    MediaResultResponse.model_validate(item.media)
                    if item.media is not None
                    else None
                ),
                intent=(
                    IntentResultResponse.model_validate(item.intent)
                    if item.intent is not None
                    else None
                ),
            )
        )
    return MessageAnalysisBatchResponse(results=tuple(results))
