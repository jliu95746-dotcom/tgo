"""Authenticated intent-analysis API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_current_project_id, get_db
from app.runtime.routing.policy import IntentRoutingPolicy
from app.schemas.intent import IntentClassificationResult
from app.schemas.intent_analysis import IntentAnalysisRequest
from app.services.chat_service import ChatService
from app.services.intent_classifier import IntentClassifier
from app.services.structured_output_chat_client import (
    ChatServiceStructuredOutputClient,
)


router = APIRouter()


@router.post(  # type: ignore[misc]
    "/intent",
    response_model=IntentClassificationResult,
    summary="分类客户意图并执行安全路由策略",
)
async def classify_intent(
    request: IntentAnalysisRequest,
    project_id: uuid.UUID = Depends(get_current_project_id),
    db: AsyncSession = Depends(get_db),
) -> IntentClassificationResult:
    """Use a project-owned provider; automated routes remain feature-gated."""
    structured_client = ChatServiceStructuredOutputClient(
        chat_service=ChatService(db),
        project_id=project_id,
        provider_id=request.provider_id,
        model=request.model,
        max_output_tokens=settings.intent_max_output_tokens,
    )
    classifier = IntentClassifier(
        structured_client,
        policy=IntentRoutingPolicy(
            automated_routes_enabled=settings.intent_automation_enabled
        ),
        provider_timeout_seconds=settings.intent_provider_timeout_seconds,
        max_input_characters=settings.intent_max_input_characters,
        max_response_characters=settings.intent_max_response_characters,
    )
    return await classifier.classify_input(request.classification_input)
