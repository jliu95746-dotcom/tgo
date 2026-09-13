"""Authenticated API contracts for intent classification."""

from __future__ import annotations

import json
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

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

    @field_validator("classification_input", mode="before")
    @classmethod
    def parse_customer_json(cls, value: object) -> object:
        # FastAPI has already decoded JSON to dict/list. Re-enter JSON mode so
        # strict tuples/enums accept their wire representation, not coercions.
        if isinstance(value, dict):
            return IntentClassificationInput.model_validate_json(
                json.dumps(value, ensure_ascii=False, allow_nan=False)
            )
        return value
