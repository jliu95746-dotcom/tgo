"""Explicit delivery receipts for staff/assist messages."""

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from app.schemas.humanization import ConversationTurn


class AssistTrainingIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_name: str = Field(
        min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$"
    )
    customer_message: str = Field(min_length=1, max_length=10000)
    ai_draft: str = Field(min_length=1, max_length=10000)
    source_message_id: str | None = Field(default=None, max_length=255)
    recent_messages: list[ConversationTurn] = Field(default_factory=list, max_length=12)


class StaffDeliveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_id: str = Field(min_length=1, max_length=255)
    channel_type: Literal[251] = 251
    client_msg_no: str = Field(min_length=1, max_length=100)
    payload: dict[str, JsonValue]
    retry_failed: bool = False
    training: AssistTrainingIntent | None = None

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if type(value.get("type")) is not int:
            raise ValueError("消息类型必须是整数")
        if len(json.dumps(value, ensure_ascii=False)) > 262144:
            raise ValueError("消息内容过长，请将大文件作为附件上传")
        if value.get("type") == 1:
            content = value.get("content")
            if not isinstance(content, str):
                raise ValueError("文本消息正文必须是字符串")
            if not content.strip():
                raise ValueError("不能发送空白消息")
        return value


class StaffDeliveryResponse(BaseModel):
    client_msg_no: str
    delivery_status: Literal["processing", "pending", "sent", "failed", "unknown"]
    history_status: Literal["pending", "sent"]
    error_code: str | None = None
    error_message: str | None = None
    training_status: Literal["none", "pending", "saved", "unavailable"] = "none"
    training_skill_name: str | None = None


class StaffDeliveryRecord(BaseModel):
    request: StaffDeliveryRequest
    receipt: StaffDeliveryResponse
    created_at: datetime
