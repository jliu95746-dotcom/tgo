"""Private contract for native Enterprise WeChat customer-service events."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NativePlatformRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform_id: UUID


class NativeContextEvent(NativePlatformRequest):
    external_userid: str = Field(min_length=1, max_length=128)
    event_id: str = Field(min_length=1, max_length=255)
    kind: Literal["state", "customer", "staff"]
    service_state: Literal[0, 1, 2, 3, 4]
    servicer_userid: str = Field(default="", max_length=128)
    content: str = Field(default="", max_length=8000)
    source_type: str = Field(default="text", max_length=40)
    sent_at: int | None = None


class NativeContextResult(BaseModel):
    visitor_id: UUID
    service_mode: Literal["auto", "assist", "manual"]
    mirrored: bool = False


class NativePendingVisitor(BaseModel):
    visitor_id: UUID
    external_userid: str
    release_requested: bool = False


class NativeAssistNotification(BaseModel):
    visitor_id: UUID
    event_id: str


class NativePendingResult(BaseModel):
    visitors: list[NativePendingVisitor]
    assist_notifications: list[NativeAssistNotification] = Field(default_factory=list)
