"""Telegram Bot outbound adapter.

Sends messages to Telegram using the Bot API sendMessage endpoint.
"""

from __future__ import annotations
import logging

from app.domain.entities import StreamEvent
from app.domain.services.adapters.base import (
    BasePlatformAdapter,
    provider_receipt,
    DeliveryRejected,
)
from app.core.config import settings

from app.api.telegram_utils import telegram_send_text

logger = logging.getLogger(__name__)


class TelegramAdapter(BasePlatformAdapter):
    """Outbound adapter for Telegram Bot.

    - Non-streaming: sends the final aggregated content via sendMessage API
    - Uses chat_id from incoming message to reply (required)
    - Requires bot_token for authentication

    Docs:
    - sendMessage: https://core.telegram.org/bots/api#sendmessage
    """

    supports_stream = False

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        parse_mode: str | None = None,
        http_timeout: int | None = None,
    ) -> None:
        """Initialize Telegram adapter.

        Args:
            bot_token: Bot token from @BotFather
            chat_id: Target chat ID for replies
            parse_mode: Optional message parsing mode (Markdown, HTML)
            http_timeout: Request timeout in seconds
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.parse_mode = parse_mode
        self.http_timeout = http_timeout or settings.request_timeout_seconds

    async def send_incremental(self, ev: StreamEvent) -> None:
        # Telegram adapter does not support streaming output; ignore incremental events
        return

    async def send_final(self, content: dict) -> None:
        """Send final message to Telegram chat.

        Args:
            content: Dict with 'text' key containing the message

        Raises:
            RuntimeError: If bot_token or chat_id is missing
        """
        text = (content or {}).get("text") or ""
        logger.debug("Telegram reply prepared")

        if content.get("delivery_fenced") and len(text) > 4096:
            raise DeliveryRejected("Reply exceeds channel message limit")
        if not text:
            logger.debug("Telegram empty reply skipped")
            return

        if not self.bot_token:
            raise RuntimeError("Telegram adapter requires bot_token")

        if not self.chat_id:
            raise RuntimeError("Telegram adapter requires chat_id")

        logger.debug("Telegram reply sending")

        # Send text message via Telegram Bot API
        result = await telegram_send_text(
            bot_token=self.bot_token,
            chat_id=self.chat_id,
            text=text[:4096],  # Telegram text limit
            parse_mode=self.parse_mode,
            timeout=self.http_timeout,
        )
        logger.debug("Telegram reply send completed")
        self.delivery_receipt = provider_receipt("telegram", result)
