"""Structured-output adapter backed by the existing project LLM service."""

from __future__ import annotations

import uuid
from typing import Protocol

from app.runtime.structured_output.base import (
    StructuredOutputClient,
    StructuredOutputRequest,
)
from app.schemas.chat import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    ResponseFormat,
)


class ChatCompletionService(Protocol):
    """Narrow interface implemented by ChatService."""

    async def create_completion(
        self,
        request: ChatCompletionRequest,
        project_id: uuid.UUID,
    ) -> ChatCompletionResponse: ...


class ChatServiceStructuredOutputClient(StructuredOutputClient):
    """Use an authenticated project's configured LLM for strict JSON output."""

    def __init__(
        self,
        *,
        chat_service: ChatCompletionService,
        project_id: uuid.UUID,
        provider_id: uuid.UUID,
        model: str,
        max_output_tokens: int = 1024,
    ) -> None:
        normalized_model = model.strip()
        if not normalized_model:
            raise ValueError("structured-output model cannot be empty")
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        self._chat_service = chat_service
        self._project_id = project_id
        self._provider_id = provider_id
        self._model = normalized_model
        self._max_output_tokens = max_output_tokens

    async def generate(self, request: StructuredOutputRequest) -> str:
        """Generate JSON without enabling tools or agentic execution."""
        system_prompt = (
            f"{request.system_prompt}\n"
            f"Schema name: {request.schema_name}\n"
            f"JSON Schema: {request.json_schema}"
        )
        completion_request = ChatCompletionRequest(
            provider_id=self._provider_id,
            model=self._model,
            messages=[
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=request.user_prompt),
            ],
            stream=False,
            temperature=0.0,
            max_tokens=self._max_output_tokens,
            response_format=ResponseFormat(type="json_object"),
            tools=None,
            tool_ids=None,
            collection_ids=None,
            auto_execute_tools=False,
            max_tool_rounds=1,
        )
        response = await self._chat_service.create_completion(
            completion_request,
            self._project_id,
        )
        if not response.choices:
            raise RuntimeError(
                "structured-output provider returned no choices"
            )
        content = response.choices[0].message.content
        if content is None or not content.strip():
            raise RuntimeError(
                "structured-output provider returned empty content"
            )
        return str(content)
