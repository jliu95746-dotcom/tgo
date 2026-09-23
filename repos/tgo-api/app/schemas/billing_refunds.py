"""Explicit human-reviewed refund amounts and entitlement disposition."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class RefundCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: UUID
    amount: int = Field(ge=1, le=100000000, strict=True)
    reason: str = Field(min_length=5, max_length=500)
    entitlement_action: Literal["keep", "suspend"]


class RefundDisposition(BaseModel):
    action: Literal["keep", "suspend"]
    original_order_amount: int
    already_refunded: int
    available_ai_from_order: int
    granted_ai_from_order: int
    company_status: str
    subscription_version: int | None
    order_kind: str
    manual_review_required: bool = True


class RefundResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    number: str
    project_id: UUID
    order_id: UUID
    amount: int
    reason: str
    status: str
    disposition: RefundDisposition
    provider_id: str | None
    succeeded_at: datetime | None
    created_at: datetime


class RefundConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewed: Literal[True]


class RefundAmount(BaseModel):
    total: int = Field(ge=1, strict=True)
    refund: int = Field(ge=1, strict=True)
    currency: str = "CNY"


class ProviderRefund(BaseModel):
    refund_id: str
    out_refund_no: str
    out_trade_no: str
    transaction_id: str
    status: Literal["SUCCESS", "CLOSED", "PROCESSING", "ABNORMAL"] = Field(
        validation_alias=AliasChoices("status", "refund_status")
    )
    success_time: datetime | None = None
    amount: RefundAmount
    mchid: str | None = None
