from __future__ import annotations
from abc import ABC
import logging
from app.domain.entities import StreamEvent
from app.domain.ports import PlatformAdapter

logger = logging.getLogger(__name__)


class BasePlatformAdapter(PlatformAdapter, ABC):
    supports_stream: bool = False


class SimpleStdoutAdapter(BasePlatformAdapter):
    """Minimal adapter for MVP/testing without logging response contents."""

    async def send_incremental(self, ev: StreamEvent) -> None:  # pragma: no cover - side effect
        logger.debug("Incremental adapter event received")

    async def send_final(self, content: dict) -> None:  # pragma: no cover - side effect
        logger.debug("Final adapter response received")

