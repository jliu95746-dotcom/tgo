"""Record each provider execution, including retries within a chat tool loop."""

from collections.abc import AsyncIterator, Awaitable, Callable
from functools import wraps
from typing import TYPE_CHECKING

from app.models.llm_provider import LLMProvider
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse
from app.services.model_usage import track_model_usage
from app.services.commercial_ai_limits import constrain_chat

if TYPE_CHECKING:
    from app.services.chat_service import ChatService


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
        service: "ChatService", request: ChatCompletionRequest, provider: LLMProvider
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
) -> Callable[["ChatService", ChatCompletionRequest, LLMProvider], AsyncIterator[str]]:
    @wraps(operation)
    async def tracked(
        service: "ChatService", request: ChatCompletionRequest, provider: LLMProvider
    ) -> AsyncIterator[str]:
        request = constrain_chat(request)
        async with track_model_usage(
            str(provider.project_id), request.model, "chat"
        ) as usage:
            completed = False
            async for chunk in operation(service, request, provider):
                if chunk.strip() == "data: [DONE]":
                    completed = True
                yield chunk
            if not completed:
                usage.failed()
            # Existing stream protocol has no reliable aggregate usage metrics.
            # Preserve unknown values instead of estimating from visible text.

    return tracked
