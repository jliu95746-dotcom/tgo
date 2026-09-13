"""Versioned, credential-free delivery state kept with a timed-out queue entry."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

NoticeStatus = Literal[
    "pending", "sending", "sent", "unknown", "failed", "cancelled", "not_required"
]


class QueueTimeoutNotice(BaseModel):
    version: Literal[1] = 1
    platform_id: UUID
    platform_type: str
    recipient: str
    history_status: NoticeStatus = "pending"
    external_status: NoticeStatus = "pending"
    history_attempts: int = 0
    external_attempts: int = 0
    history_error: str | None = None
    external_error: str | None = None
    next_retry_at: datetime = Field(default_factory=datetime.utcnow)


QUEUE_TIMEOUT_NOTICE_KEY = "queue_timeout_notice"
QUEUE_TIMEOUT_TEXT = "等候超时，这次排队已结束。还需要人工的话，可以重新申请。"
