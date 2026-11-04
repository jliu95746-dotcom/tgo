"""Bridge polling is scoped to the original WeChat visitor identity."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WeChatDeliveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform_api_key: str = Field(min_length=1, repr=False)
    channel_id: str = Field(min_length=1, max_length=255)
    channel_type: Literal[251] = 251
    attempt_id: UUID | None = None


class WeChatDeliveryClaim(WeChatDeliveryRequest):
    attempt_id: UUID


class WeChatDeliveryReceipt(WeChatDeliveryRequest):
    claim_id: UUID
    result: Literal["sent", "unknown"]
    receipt: str | None = Field(default=None, min_length=1, max_length=512)


class WeChatDeliveryReply(BaseModel):
    delivery_id: UUID
    client_msg_no: str
    text: str
    created_at: datetime


class WeChatDeliveryList(BaseModel):
    replies: list[WeChatDeliveryReply]


class WeChatDeliveryState(BaseModel):
    state: str


class WeChatDeliveryClaimResult(BaseModel):
    claim_id: UUID


class WeChatDeliveryAccepted(BaseModel):
    accepted: bool = True
