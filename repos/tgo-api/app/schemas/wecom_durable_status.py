"""Aggregate stored WeCom customer-service work without tenant identifiers."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DurableCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queued: int = Field(ge=0)
    processing: int = Field(ge=0)
    terminal_failure: int = Field(ge=0)
    completed: int = Field(ge=0)
    other: int = Field(ge=0)
    stale_lease: int = Field(ge=0)


class FailureNoticeCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pending: int = Field(ge=0)
    sending: int = Field(ge=0)
    submitted: int = Field(ge=0)
    unknown: int = Field(ge=0)
    needs_attention: int = Field(ge=0)


class WeComDurableStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checked_at: datetime
    inbox: DurableCounts
    media_jobs: DurableCounts
    failure_notices: FailureNoticeCounts
