"""Versioned humanization training and reply context contracts."""

from typing import Literal
from pydantic import BaseModel, Field


class ConversationTurn(BaseModel):
    role: Literal["customer", "staff", "assistant"]
    content: str = Field(max_length=2000)


class TrainingExample(BaseModel):
    id: str
    customer_message: str
    ai_draft: str
    final_reply: str
    source_message_id: str = ""
    created_at: str = ""
    recent_messages: list[ConversationTurn] = Field(default_factory=list)
    scene_tags: list[str] = Field(default_factory=list, max_length=8)
    change_kind: Literal["expression", "facts", "mixed", "review"] = "review"
    rules: list[str] = Field(default_factory=list, max_length=8)
    warnings: list[str] = Field(default_factory=list)
    selected: bool = False


class TrainingReview(BaseModel):
    name: str
    published_version: int
    snapshot_id: str
    pending: list[TrainingExample]
    published: list[TrainingExample]
    rules: list[str]
    analysis_error: str | None = None


class TrainingPublishRequest(BaseModel):
    snapshot_id: str = Field(min_length=16, max_length=64)
    samples: list[TrainingExample] = Field(min_length=1, max_length=100)


class HumanizationMatchRequest(BaseModel):
    customer_message: str = Field(min_length=1, max_length=10000)
    recent_messages: list[ConversationTurn] = Field(default_factory=list, max_length=12)
    factual_draft: str = Field(default="", max_length=10000)
    candidate: TrainingPublishRequest | None = None


class HumanizationContext(BaseModel):
    name: str
    published_version: int
    instructions: str
    rules: list[str] = Field(default_factory=list)
    examples: list[TrainingExample] = Field(default_factory=list)


class HumanizationTryRequest(HumanizationMatchRequest):
    factual_draft: str = Field(min_length=1, max_length=10000)


class HumanizationTryResponse(BaseModel):
    published_reply: str
    candidate_reply: str | None = None
    published_version: int
    matched_example_ids: list[str]
    candidate_example_ids: list[str] = Field(default_factory=list)
    quality_issues: list[str] = Field(default_factory=list)
