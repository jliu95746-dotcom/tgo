"""Immutable commercial snapshots and server-generated quotes."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

BillingKind = Literal["subscribe", "renew", "upgrade", "seats", "ai_pack"]


class PlanDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    rank: int = Field(ge=1, le=1000)
    monthly_price: int = Field(ge=1, le=100000000, strict=True)
    annual_price: int = Field(ge=1, le=100000000, strict=True)
    seats: int = Field(ge=1, le=10000, strict=True)
    monthly_ai: int = Field(ge=0, le=100000000, strict=True)
    knowledge_bytes: int = Field(ge=1, le=10000000000000, strict=True)
    channel_limit: int = Field(ge=1, le=10000, strict=True)
    seat_monthly_price: int = Field(ge=1, le=10000000, strict=True)
    seat_annual_price: int = Field(ge=1, le=10000000, strict=True)
    ai_pack_price: int = Field(ge=1, le=100000000, strict=True)
    ai_pack_replies: int = Field(ge=1, le=100000000, strict=True)

    def price(self, months: int) -> int:
        return self.annual_price if months == 12 else self.monthly_price

    def seat_price(self, months: int) -> int:
        return self.seat_annual_price if months == 12 else self.seat_monthly_price


class PlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,39}$")
    definition: PlanDefinition


class PlanResponse(BaseModel):
    id: UUID
    code: str
    version: int
    status: str
    definition: PlanDefinition


class QuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: BillingKind
    plan_id: UUID | None = None
    months: Literal[1, 12] = 1
    quantity: int = Field(default=1, ge=1, le=10000, strict=True)


class PeriodSnapshot(BaseModel):
    id: UUID
    start: datetime
    end: datetime
    months: Literal[1, 12]
    current_price: int
    definition: PlanDefinition


class QuoteDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: BillingKind
    plan_id: UUID
    plan_code: str
    definition: PlanDefinition
    months: Literal[1, 12]
    quantity: int
    quoted_at: datetime
    subscription_expires_at: datetime | None
    starts_at: datetime
    ends_at: datetime
    periods: list[PeriodSnapshot] = Field(default_factory=list)
    extra_seats: int = 0
    resulting_seats: int
    additional_ai: int = 0
    anchor_day: int


class QuoteResponse(BaseModel):
    id: UUID
    amount: int
    currency: Literal["CNY"] = "CNY"
    expires_at: datetime
    details: QuoteDetails


class OrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quote_id: UUID


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    number: str
    amount: int
    payment_status: str
    fulfillment_status: str
    paid_at: datetime | None
    created_at: datetime
    code_url: str | None
    expires_at: datetime
    refunded_amount: int
    project_id: UUID
    failure_code: str | None
    transaction_id: str | None


class SubscriptionResponse(BaseModel):
    status: str
    expires_at: datetime | None
    plan: PlanResponse | None
    seats: int | None
    seats_used: int
    seats_reserved: int
    ai_remaining: int
    server_time: datetime
    months: int | None
    version: int | None
