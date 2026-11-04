"""Scoped human review of server-defined knowledge cases, never activation."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.schemas.agent_trials import TrialApplicability


class KnowledgeReviewCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    template_id: Literal["knowledge", "aftersales"]
    template_version: int = Field(ge=1)
    criteria_version: Literal[1] = 1
    question: str = Field(min_length=1, max_length=4000)
    expected_result: Literal["grounded_answer", "safe_refusal"]
    valid_from: datetime | None = None


class KnowledgeReviewChecks(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer_correct: StrictBool = False
    evidence_checked: StrictBool = False
    no_unverified_claims: StrictBool = False
    expected_behavior: StrictBool = False

    def confirmed(self) -> bool:
        return all(self.model_dump().values())


class KnowledgeReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    case_id: str = Field(min_length=1, max_length=80)
    decision: Literal["passed", "rejected"]
    checks: KnowledgeReviewChecks = Field(default_factory=KnowledgeReviewChecks)
    note: str = Field(default="", max_length=1000)
    expected_latest_review_id: UUID | None = None


class KnowledgeReviewCommand(KnowledgeReviewRequest):
    """Only the authenticated gateway supplies the current case definition."""
    case: KnowledgeReviewCase
    current_cases: list[KnowledgeReviewCase] | None = Field(
        default=None, max_length=30
    )


class KnowledgeReviewQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cases: list[KnowledgeReviewCase] = Field(max_length=30)


class KnowledgeReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    revision: int = Field(ge=1)
    agent_id: UUID
    report_id: UUID
    request_key: UUID
    reviewed_by: UUID
    scope: Literal["knowledge_case"] = "knowledge_case"
    can_enable: Literal[False] = False
    decision: Literal["passed", "rejected"]
    checks: KnowledgeReviewChecks
    note: str
    case: KnowledgeReviewCase
    case_status: Literal["current", "changed"]
    applicability: TrialApplicability
    superseded: bool = False
    effective: bool = False
    created_at: datetime
    valid_until: datetime


class KnowledgeReviewState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: UUID
    report_id: UUID
    scope: Literal["knowledge_case"] = "knowledge_case"
    can_enable: Literal[False] = False
    available_cases: list[KnowledgeReviewCase]
    applicability: TrialApplicability
    can_review: bool
    valid_until: datetime
    reviews: list[KnowledgeReviewResponse]
