from __future__ import annotations

from app.domain.entities import StreamEvent
from app.domain.services.adapters.base import BasePlatformAdapter, provider_receipt, DeliveryRejected
from app.core.config import settings

from app.api.wecom_utils import wecom_bot_send_response_markdown


class WeComBotAdapter(BasePlatformAdapter):
    """Outbound adapter for WeCom Bot (企业微信智能机器人).

    - Non-streaming: sends the final aggregated content via response_url
    - Uses response_url to reply to messages (required)
    - Only supports markdown format (text type is not supported by the API)

    Docs:
    - 智能机器人: https://developer.work.weixin.qq.com/document/path/100719
    - 主动回复消息: https://developer.work.weixin.qq.com/document/path/101138
    """

    supports_stream = False

    def __init__(
        self,
        response_url: str = "",
        http_timeout: int | None = None,
    ) -> None:
        self.response_url = response_url
        self.http_timeout = http_timeout or settings.request_timeout_seconds

    async def send_incremental(self, ev: StreamEvent) -> None:
        # WeCom Bot adapter does not support streaming output; ignore incremental events
        return

    async def send_final(self, content: dict) -> None:
        text = (content or {}).get("text") or ""
        if content.get("delivery_fenced") and len(text) > 20480:
            raise DeliveryRejected("Reply exceeds channel message limit")
        if not text:
            # Nothing to send
            return

        if not self.response_url:
            raise RuntimeError("WeCom Bot adapter requires response_url")

        # Always use markdown for response_url (text type is not supported by API)
        result = await wecom_bot_send_response_markdown(
            response_url=self.response_url,
            content=text[:20480],
            timeout=self.http_timeout,
        )
        self.delivery_receipt = provider_receipt("wecom_bot", result)
