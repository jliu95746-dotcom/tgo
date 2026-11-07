from enum import Enum, IntEnum
from typing import Final

# WuKongIM channel types
CHANNEL_TYPE_CUSTOMER_SERVICE: Final = 251
CHANNEL_TYPE_PROJECT_STAFF = 249  # 项目坐席群组频道
SETUP_DEFAULT_AGENT_MODEL = "default"
STAFF_AVATAR_MAX_BYTES = 5 * 1024 * 1024
PUBLIC_AI_FAILURE_MESSAGE = "AI 暂时无法完成回复，请稍后重试或联系人工客服。"
AI_FALLBACK_RETRYABLE_FAILURE = "ai_fallback_retryable"
AI_INTERACTION_FAILURE_CODES = frozenset({
    "ai_processing_failed",
    "ai_workflow_failed",
    "ai_stream_failed",
    "ai_stream_cancelled",
    "ai_background_cancelled",
    "business_context_unavailable",
    "visitor_access_ended",
})

# member_type
MEMBER_TYPE_STAFF = "staff"
MEMBER_TYPE_VISITOR = "visitor"


class MessageType(IntEnum):
    """WuKongIM message type constants."""
    # Standard message types (1-999)
    TEXT = 1
    IMAGE = 2
    FILE = 3
    VOICE = 4
    VIDEO = 5

    # System message types (1000-2000 reserved for system notifications)
    STAFF_ASSIGNED = 1000
    SESSION_CLOSED = 1001
    SESSION_TRANSFERRED = 1002
    MEMORY_CLEARED = 1003
    HUMAN_HANDOFF_REQUESTED = 1004
    QUEUE_TIMEOUT = 1005
