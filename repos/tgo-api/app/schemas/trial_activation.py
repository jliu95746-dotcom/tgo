"""Public contracts for operator-issued trial activation codes."""

from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from app.schemas.base import BaseSchema


class TrialCodeRecord(BaseSchema):
    id: UUID
    created_at: datetime
    expires_at: datetime
    redeemed_at: datetime | None
    redeemed_project_id: UUID | None


class TrialCodeIssue(TrialCodeRecord):
    code: str


class TrialRedeemRequest(BaseSchema):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=25, max_length=64, repr=False)
