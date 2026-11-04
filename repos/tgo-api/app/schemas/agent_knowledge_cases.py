"""Employee-owned question catalogs; evaluation rules remain server-defined."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.agent_trial_reviews import KnowledgeReviewCase


class KnowledgeCatalogWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    expected_revision: int = Field(ge=0, strict=True)
    questions: list[str] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def valid_questions(self) -> "KnowledgeCatalogWrite":
        self.questions = [question.strip() for question in self.questions]
        if any(
            not question or len(question) > 4000 for question in self.questions
        ):
            raise ValueError("每个测试问题需为 1 至 4000 个字符")
        if len(set(self.questions)) != 6:
            raise ValueError("测试问题不能重复")
        return self


class KnowledgeCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: UUID
    id: UUID | None = None
    revision: int = Field(ge=0)
    request_key: UUID | None = None
    configured_by: UUID | None = None
    created_at: datetime | None = None
    current: bool = True
    cases: list[KnowledgeReviewCase] = Field(min_length=6, max_length=6)
