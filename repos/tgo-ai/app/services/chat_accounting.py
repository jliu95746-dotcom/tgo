"""Record every provider execution within a chat tool loop."""

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from functools import wraps
from contextvars import ContextVar
from typing import TYPE_CHECKING

from app.models.llm_provider import LLMProvider
from app.schemas.chat import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    Usage,
)
from app.services.model_usage import UsageTracker, track_model_usage
from app.services.commercial_ai_limits import constrain_chat

if TYPE_CHECKING:
    from app.services.chat_service import ChatService

_stream_usage: ContextVar[UsageTracker | None] = ContextVar(
    "chat_stream_usage", default=None
)


def observe_stream_usage(measured: Usage | None) -> None:
    tracker = _stream_usage.get()
    if tracker is not None and measured is not None:
        tracker.input_tokens = measured.prompt_tokens
        tracker.output_tokens = measured.completion_tokens


async def close_provider_stream(stream: AsyncIterator[str]) -> None:
    if isinstance(stream, AsyncGenerator):
        await stream.aclose()


def account_completion(
    operation: Callable[
        ["ChatService", ChatCompletionRequest, LLMProvider],
        Awaitable[ChatCompletionResponse],
    ],
) -> Callable[
    ["ChatService", ChatCompletionRequest, LLMProvider],
    Awaitable[ChatCompletionResponse],
]:
    @wraps(operation)
    async def tracked(
        service: "ChatService",
        request: ChatCompletionRequest,
        provider: LLMProvider,
    ) -> ChatCompletionResponse:
        request = constrain_chat(request)
        async with track_model_usage(
            str(provider.project_id), request.model, "chat"
        ) as usage:
            response = await operation(service, request, provider)
            if response.usage is not None:
                usage.input_tokens = response.usage.prompt_tokens
                usage.output_tokens = response.usage.completion_tokens
            return response

    return tracked


def account_stream(
    operation: Callable[
        ["ChatService", ChatCompletionRequest, LLMProvider], AsyncIterator[str]
    ],
) -> Callable[
    ["ChatService", ChatCompletionRequest, LLMProvider], AsyncIterator[str]
]:
    @wraps(operation)
    async def tracked(
        service: "ChatService",
        request: ChatCompletionRequest,
        provider: LLMProvider,
    ) -> AsyncIterator[str]:
        request = constrain_chat(request)
        async with track_model_usage(
            str(provider.project_id), request.model, "chat"
        ) as usage:
            completed = False
            token = _stream_usage.set(usage)
            chunks = operation(service, request, provider)
            try:
                async for chunk in chunks:
                    if chunk.strip() == "data: [DONE]":
                        completed = True
                    yield chunk
                if not completed:
                    usage.failed()
            finally:
                try:
                    await close_provider_stream(chunks)
                finally:
                    _stream_usage.reset(token)

    return tracked
