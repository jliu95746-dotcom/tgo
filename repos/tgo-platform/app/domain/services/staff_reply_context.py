"""Recover server-owned reply context for staff/assist messages."""

from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DingTalkInbox, FeishuInbox, Platform
from app.domain.entities import NormalizedMessage


class StaffReplyContextError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


async def build_staff_reply_message(
    db: AsyncSession,
    *,
    platform: Platform,
    recipient: str,
    text: str,
    client_msg_no: str,
) -> NormalizedMessage:
    """Use the same context/adapter contract as inbound AI replies.

    Visitor identity currently groups messages by platform and sender, not by
    external group chat. A group callback is therefore not a safe destination
    for a private staff conversation until per-conversation routing is stored.
    """
    platform_type = (platform.type or "").lower()
    if not recipient:
        raise StaffReplyContextError(
            "DELIVERY_CONTEXT_MISSING",
            "找不到客户的渠道身份，请让客户重新发送一条消息后再试。",
        )

    reply_context: dict[str, str]
    if platform_type == "feishu_bot":
        config = platform.config or {}
        if not (config.get("app_id") and config.get("app_secret")):
            raise StaffReplyContextError(
                "PLATFORM_CONFIG_INVALID",
                "飞书应用配置不完整，请检查 App ID 和 App Secret。",
                400,
            )
        inbox = await db.scalar(
            select(FeishuInbox)
            .where(
                FeishuInbox.platform_id == platform.id,
                FeishuInbox.from_user == recipient,
            )
            .order_by(
                FeishuInbox.received_at.desc().nullslast(),
                FeishuInbox.fetched_at.desc(),
            )
            .limit(1)
        )
        if not inbox or not inbox.message_id:
            raise StaffReplyContextError(
                "DELIVERY_CONTEXT_MISSING",
                "缺少飞书回复所需的消息记录，请让客户重新发送一条消息后再试。",
            )
        if inbox.chat_type != "p2p":
            raise StaffReplyContextError(
                "DELIVERY_CONTEXT_AMBIGUOUS",
                "最近一条飞书消息来自群聊，当前客服会话无法确认目标群。请让客户私聊机器人后再回复。",
            )
        context_key = "feishu"
        reply_context = {"message_id": inbox.message_id, "chat_id": inbox.chat_id or ""}
    elif platform_type == "dingtalk_bot":
        inbox = await db.scalar(
            select(DingTalkInbox)
            .where(
                DingTalkInbox.platform_id == platform.id,
                DingTalkInbox.from_user == recipient,
            )
            .order_by(
                DingTalkInbox.received_at.desc().nullslast(),
                DingTalkInbox.fetched_at.desc(),
            )
            .limit(1)
        )
        if not inbox or not inbox.session_webhook:
            raise StaffReplyContextError(
                "DELIVERY_CONTEXT_MISSING",
                "缺少钉钉回复地址，请让客户重新发送一条消息后再试。",
            )
        if inbox.conversation_type != "1":
            raise StaffReplyContextError(
                "DELIVERY_CONTEXT_AMBIGUOUS",
                "最近一条钉钉消息来自群聊，当前客服会话无法确认目标群。请让客户私聊机器人后再回复。",
            )
        expires_at = inbox.session_webhook_expired_time
        if expires_at is None or expires_at <= int(time.time() * 1000):
            raise StaffReplyContextError(
                "DELIVERY_CONTEXT_EXPIRED",
                "钉钉回复地址已过期或缺少有效期，请让客户重新发送一条消息后再试。",
            )
        context_key = "dingtalk"
        reply_context = {
            "session_webhook": inbox.session_webhook,
            "conversation_id": inbox.conversation_id or "",
        }
    else:
        raise StaffReplyContextError(
            "PLATFORM_TYPE_UNSUPPORTED", "该渠道不支持恢复回复会话。", 400
        )

    return NormalizedMessage(
        source=context_key,
        from_uid=recipient,
        content=text,
        platform_api_key=platform.api_key,
        platform_type=platform_type,
        platform_id=str(platform.id),
        extra={"message_id": client_msg_no, "msg_type": 1, context_key: reply_context},
    )
