"""One immutable notification intent per persisted human-service request."""

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models import ManualServiceRequest, Visitor
from app.utils.const import MessageType

NOTICE_KEY = "handoff_notification"
NoticeStatus = Literal["pending", "sending", "sent", "unknown", "skipped"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class HandoffNotice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    state: NoticeStatus = "pending"
    client_msg_no: str
    channel_id: str
    channel_type: int
    platform_id: UUID
    mode: str | None
    ai_disabled: bool | None
    receipt_status: Literal["active", "waiting"]
    session_id: UUID | None = None
    entry_id: UUID | None = None
    assigned_staff_id: UUID | None = None
    content: str = Field(min_length=1, max_length=4096)
    extra: list[dict[str, str]] | None = None
    attempts: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=utc_now)
    last_error: str | None = None

    def payload(self) -> dict[str, object]:
        value: dict[str, object] = {
            "type": MessageType.HUMAN_HANDOFF_REQUESTED,
            "content": self.content,
        }
        if self.extra:
            value["extra"] = self.extra
        return value


def load_notice(request: ManualServiceRequest) -> HandoffNotice | None:
    value = (request.request_metadata or {}).get(NOTICE_KEY)
    try:
        return HandoffNotice.model_validate(value)
    except ValidationError:
        return None


def save_notice(request: ManualServiceRequest, notice: HandoffNotice) -> None:
    request.request_metadata = {
        **(request.request_metadata or {}),
        NOTICE_KEY: notice.model_dump(mode="json"),
        "notification_status": "submitted" if notice.state == "sent"
        else "unknown",
    }


def stage_handoff_notice(
    request: ManualServiceRequest,
    visitor: Visitor,
    receipt: dict[str, object],
    *,
    content: str,
    channel_id: str,
    channel_type: int,
    extra: list[dict[str, str]] | None = None,
) -> None:
    """Commit the intent with its receipt before any external send."""
    if NOTICE_KEY in (request.request_metadata or {}):
        return
    notice = HandoffNotice.model_validate({
        "client_msg_no": f"handoff-{request.id}",
        "channel_id": channel_id, "channel_type": channel_type,
        "platform_id": visitor.platform_id, "mode": visitor.service_mode,
        "ai_disabled": visitor.ai_disabled,
        "receipt_status": receipt.get("status"),
        "session_id": receipt.get("session_id"),
        "entry_id": receipt.get("entry_id"),
        "assigned_staff_id": receipt.get("assigned_staff_id"),
        "content": content, "extra": extra,
    })
    save_notice(request, notice)
