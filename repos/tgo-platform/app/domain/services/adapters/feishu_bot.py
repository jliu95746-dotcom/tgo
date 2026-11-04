"""Feishu Bot outbound adapter.

Sends messages to Feishu using the Reply API.
"""
from __future__ import annotations

from app.domain.entities import StreamEvent
from app.domain.services.adapters.base import BasePlatformAdapter, provider_receipt, DeliveryRejected
from app.core.config import settings

from app.api.feishu_utils import feishu_get_tenant_access_token, feishu_reply_text


class FeishuBotAdapter(BasePlatformAdapter):
    """Outbound adapter for Feishu Bot (飞书机器人).

    - Non-streaming: sends the final aggregated content via Reply API
    - Uses message_id to reply to messages (required)
    - Requires app_id and app_secret for authentication

    Docs:
    - Reply API: https://open.feishu.cn/document/server-docs/im-v1/message/reply
    """

    supports_stream = False

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        message_id: str,
        http_timeout: int | None = None,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.message_id = message_id  # Required for reply API
        self.http_timeout = http_timeout or settings.request_timeout_seconds

    async def send_incremental(self, ev: StreamEvent) -> None:
        # Feishu Bot adapter does not support streaming output; ignore incremental events
        return

    async def send_final(self, content: dict) -> None:
        text = (content or {}).get("text") or ""
        if content.get("delivery_fenced") and len(text) > 30000:
            raise DeliveryRejected("Reply exceeds channel message limit")
        if not text:
            # Nothing to send
            return

        if not self.message_id:
            raise RuntimeError("Feishu Bot adapter requires message_id")

        if not (self.app_id and self.app_secret):
            raise RuntimeError("Feishu Bot adapter requires app_id and app_secret")

        # Get access token
        access_token = await feishu_get_tenant_access_token(
            app_id=self.app_id,
            app_secret=self.app_secret,
            timeout=self.http_timeout,
        )

        # Reply to the message
        result = await feishu_reply_text(
            access_token=access_token,
            message_id=self.message_id,
            text=text[:30000],  # Feishu text limit is around 30KB
            timeout=self.http_timeout,
        )
        self.delivery_receipt = provider_receipt("feishu", result)
