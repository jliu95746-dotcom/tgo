"""DingTalk Bot outbound adapter.

Sends messages to DingTalk using the sessionWebhook.
"""
from __future__ import annotations

from app.domain.entities import StreamEvent
from app.domain.services.adapters.base import BasePlatformAdapter, provider_receipt, DeliveryRejected
from app.core.config import settings

from app.api.dingtalk_utils import dingtalk_send_text


class DingTalkBotAdapter(BasePlatformAdapter):
    """Outbound adapter for DingTalk Bot (钉钉机器人).

    - Non-streaming: sends the final aggregated content via sessionWebhook
    - Uses session_webhook from callback message to reply (required)

    Docs:
    - Robot messages: https://open.dingtalk.com/document/orgapp/the-robot-sends-a-group-message
    """

    supports_stream = False

    def __init__(
        self,
        session_webhook: str,
        http_timeout: int | None = None,
    ) -> None:
        self.session_webhook = session_webhook  # Required for replying
        self.http_timeout = http_timeout or settings.request_timeout_seconds

    async def send_incremental(self, ev: StreamEvent) -> None:
        # DingTalk Bot adapter does not support streaming output; ignore incremental events
        return

    async def send_final(self, content: dict) -> None:
        text = (content or {}).get("text") or ""
        if content.get("delivery_fenced") and len(text) > 20000:
            raise DeliveryRejected("Reply exceeds channel message limit")
        if not text:
            # Nothing to send
            return

        if not self.session_webhook:
            raise RuntimeError("DingTalk Bot adapter requires session_webhook")

        # Send text message via sessionWebhook
        result = await dingtalk_send_text(
            session_webhook=self.session_webhook,
            content=text[:20000],  # DingTalk text limit
            timeout=self.http_timeout,
        )
        self.delivery_receipt = provider_receipt("dingtalk", result)
