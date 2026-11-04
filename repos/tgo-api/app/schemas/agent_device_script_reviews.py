"""Metadata for an administrator's decision on one fixed script result."""

from datetime import timedelta
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.schemas.agent_device_script_trials import Digest

ReviewDecision = Literal["approved", "rejected"]
RunCheckStatus = Literal[
    "ready",
    "review_required",
    "device_changed",
    "budget_exhausted",
    "unavailable",
]
ReviewStatus = Literal[
    "unreviewed",
    "approved",
    "rejected",
    "revoked",
    "expired",
    "changed",
]


class ScriptReviewProof(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_version_id: UUID
    case_digest: Digest
    result_digest: Digest


class ScriptReviewWrite(ScriptReviewProof):
    decision: ReviewDecision


class ScriptReviewRecord(ScriptReviewWrite):
    id: UUID
    reviewed_at: AwareDatetime
    expires_at: AwareDatetime
    revoked_at: AwareDatetime | None

    @model_validator(mode="after")
    def valid_timestamps(self) -> Self:
        if self.expires_at <= self.reviewed_at or (
            self.revoked_at is not None
            and (
                self.decision != "approved"
                or self.revoked_at < self.reviewed_at
            )
        ):
            raise ValueError("Invalid script review timeline")
        return self


class ScriptReviewState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal[
        "employee_device_script_review"
    ] = "employee_device_script_review"
    can_enable: Literal[False] = False
    project_id: UUID
    actor_id: UUID
    agent_id: UUID
    report_id: UUID
    status: ReviewStatus
    review: ScriptReviewRecord | None

    @model_validator(mode="after")
    def consistent_status(self) -> Self:
        row = self.review
        if row is None:
            if self.status != "unreviewed":
                raise ValueError("Missing review record")
        else:
            if row.revoked_at is not None:
                allowed = {"revoked"}
            elif row.decision == "rejected":
                allowed = {"rejected"}
            else:
                allowed = {"approved", "expired", "changed"}
            if self.status not in allowed:
                raise ValueError("Invalid script review status")
        return self


class ScriptRunCheck(BaseModel):
    """A short-lived observation that never reserves or authorizes a run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal[
        "employee_device_script_run_check"
    ] = "employee_device_script_run_check"
    can_enable: Literal[False] = False
    review: ScriptReviewState
    status: RunCheckStatus
    remaining_runs: Annotated[
        int, Field(strict=True, ge=0, le=100)
    ] | None
    checked_at: AwareDatetime
    valid_until: AwareDatetime | None

    @model_validator(mode="after")
    def consistent_check(self) -> Self:
        approved = self.review.review
        if self.status in {"ready", "budget_exhausted"}:
            if self.review.status != "approved" or approved is None:
                raise ValueError("Current approval required")
            if self.remaining_runs is None:
                raise ValueError("Current run count required")
        elif self.remaining_runs is not None:
            raise ValueError("Unverified count")
        if self.status == "ready":
            if (
                not self.remaining_runs
                or self.valid_until is None
                or approved is None
                or self.valid_until <= self.checked_at
                or self.valid_until > approved.expires_at
                or self.valid_until
                > self.checked_at + timedelta(seconds=30)
            ):
                raise ValueError("Invalid run check validity")
        elif self.valid_until is not None:
            raise ValueError(
                "Blocked check cannot authorize a time window"
            )
        if (
            self.status == "budget_exhausted"
            and self.remaining_runs != 0
        ):
            raise ValueError("Exhausted budget must be zero")
        if (
            self.status == "review_required"
            and self.review.status == "approved"
        ):
            raise ValueError("Current review is approved")
        return self
