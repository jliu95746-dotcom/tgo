from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Protocol, AsyncIterator

from app.domain.entities import NormalizedMessage, ChatCompletionRequest, StreamEvent


class ChannelListener(Protocol):
    async def listen(self) -> AsyncIterator[dict]:
        """Yield raw inbound message events asynchronously."""


class MessageNormalizer(Protocol):
    async def normalize(self, raw: dict) -> NormalizedMessage: ...


class TgoApiClient(Protocol):
    def chat_completion(self, req: ChatCompletionRequest) -> AsyncIterator[bytes]:
        """Open SSE stream by POSTing to tgo-api and yield raw event lines as bytes."""


class SSEManager(Protocol):
    def stream_events(self, frames: AsyncIterator[bytes]) -> AsyncIterator[StreamEvent]: ...
    async def aggregate(self, events: AsyncIterator[StreamEvent]) -> dict: ...


class PlatformAdapter(ABC):
    supports_stream: bool = False

    @abstractmethod
    async def send_incremental(self, ev: StreamEvent) -> None: ...

    @abstractmethod
    async def send_final(self, content: dict) -> None: ...

