"""Informational knowledge coverage, distinct from employee activation."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.agent_trial_reviews import KnowledgeReviewCase
from app.schemas.knowledge import KnowledgeChannel

KnowledgeCaseStatus = Literal[
    "not_tested",
    "in_progress",
    "awaiting_review",
    "needs_retest",
    "failed",
    "passed",
    "unavailable",
]


class KnowledgeCapabilityQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: KnowledgeChannel = KnowledgeChannel.WEB
    cases: list[KnowledgeReviewCase] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def unique_cases(self) -> "KnowledgeCapabilityQuery":
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate knowledge case")
        versions: dict[str, int] = {}
        for case in self.cases:
            if (
                versions.setdefault(case.template_id, case.template_version)
                != case.template_version
            ):
                raise ValueError("Inconsistent template versions")
        return self


class KnowledgeCaseCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case: KnowledgeReviewCase
    status: KnowledgeCaseStatus
    report_id: UUID | None = None
    review_id: UUID | None = None
    review_revision: int | None = Field(default=None, ge=1)
    valid_until: datetime | None = None

    @model_validator(mode="after")
    def proof_for_pass(self) -> "KnowledgeCaseCoverage":
        if self.status == "passed" and not (
            self.report_id
            and self.review_id
            and self.review_revision
            and self.valid_until
        ):
            raise ValueError("Missing knowledge review proof")
        return self


class KnowledgeCapabilityCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: Literal["knowledge", "aftersales"]
    template_version: int = Field(ge=1)
    status: KnowledgeCaseStatus
    answer_cases_passed: int = Field(ge=0)
    answer_cases_required: int = Field(ge=0)
    safety_cases_passed: int = Field(ge=0)
    safety_cases_required: int = Field(ge=0)
    cases: list[KnowledgeCaseCoverage]
    valid_until: datetime | None = None

    @model_validator(mode="after")
    def consistent_coverage(self) -> "KnowledgeCapabilityCoverage":
        answers = [
            row for row in self.cases if row.case.expected_result == "grounded_answer"
        ]
        safety = [
            row for row in self.cases if row.case.expected_result == "safe_refusal"
        ]
        actual = (
            sum(row.status == "passed" for row in answers),
            len(answers),
            sum(row.status == "passed" for row in safety),
            len(safety),
        )
        if actual != (
            self.answer_cases_passed,
            self.answer_cases_required,
            self.safety_cases_passed,
            self.safety_cases_required,
        ):
            raise ValueError("Inconsistent case counts")
        if len({row.case.id for row in self.cases}) != len(self.cases) or any(
            row.case.template_id != self.template_id
            or row.case.template_version != self.template_version
            for row in self.cases
        ):
            raise ValueError("Inconsistent template cases")
        if self.status == "passed" and (
            not answers
            or not safety
            or any(row.status != "passed" for row in self.cases)
            or not self.valid_until
        ):
            raise ValueError("Incomplete knowledge coverage")
        return self


class AgentKnowledgeCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: UUID
    reviewed_by: UUID
    channel: KnowledgeChannel
    scope: Literal["knowledge_templates"] = "knowledge_templates"
    can_enable: Literal[False] = False
    enabled: bool
    knowledge_status: Literal["available", "missing", "unknown"]
    checked_at: datetime
    capabilities: list[KnowledgeCapabilityCoverage]
