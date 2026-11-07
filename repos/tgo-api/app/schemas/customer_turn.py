"Channel-neutral turn receipts; private quota and model credentials stay \
server-side."

from typing import Literal
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TurnReceipt(BaseModel):
    turn_id: UUID
    version: int
    client_msg_no: str
    source_message_id: str
    duplicate: bool = False


class TurnDeliveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform_api_key: str = Field(min_length=1, repr=False)
    channel_id: str
    channel_type: Literal[251] = 251
    version: int = Field(ge=1)


class TurnDeliveryReceipt(TurnDeliveryRequest):
    claim_id: UUID
    result: Literal["sent", "rejected", "unknown"]
    receipt: str | None = Field(default=None, max_length=512)


class TurnReply(BaseModel):
    turn_id: UUID
    version: int
    client_msg_no: str
    text: str
    ready_at: datetime | None = None


class TurnPollRequest(BaseModel):
    platform_api_key: str = Field(min_length=1, repr=False)
    channel_id: str
    channel_type: Literal[251] = 251


class TurnClaim(BaseModel):
    turn_id: UUID
    version: int
    claim_id: UUID
    text: str
    source_message_id: str
    customer_uid: str


class TurnReplyList(BaseModel):
    replies: list[TurnReply]


class TurnState(BaseModel):
    turn_id: UUID
    version: int
    state: str


class TurnSuperseded(Exception):
    (
        'The complete turn changed before publication; no stale content may '
        'escape.'
    )
