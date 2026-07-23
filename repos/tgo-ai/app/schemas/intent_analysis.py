"""Authenticated API contracts for intent classification."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.intent import IntentClassificationInput


class IntentAnalysisRequest(BaseModel):
    """Select one project-owned LLM provider and classify customer content."""

    model_config = ConfigDict(
        extra="forbid", strict=False, str_strip_whitespace=True
    )

    provider_id: uuid.UUID
    model: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:/-]+$"
    )
    classification_input: IntentClassificationInput
