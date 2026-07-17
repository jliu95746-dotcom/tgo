"""Contract tests for the authenticated intent-analysis adapter."""

from __future__ import annotations

import uuid

import pytest

from app.runtime.structured_output.base import StructuredOutputRequest
from app.schemas.chat import ChatCompletionResponse, Choice, ChoiceMessage
from app.services.structured_output_chat_client import (
    ChatServiceStructuredOutputClient,
)


class FakeChatService:
    def __init__(self) -> None:
        self.project_id: uuid.UUID | None = None
        self.request = None

    async def create_completion(self, request, project_id):
        self.request = request
        self.project_id = project_id
        return ChatCompletionResponse(
            model=request.model,
            choices=[
                Choice(
                    index=0,
                    message=ChoiceMessage(content='{"intent":"unknown"}'),
                    finish_reason="stop",
                )
            ],
        )


@pytest.mark.asyncio
async def test_structured_output_adapter_scopes_provider_to_project() -> None:
    service = FakeChatService()
    project_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    client = ChatServiceStructuredOutputClient(
        chat_service=service,
        project_id=project_id,
        provider_id=provider_id,
        model="test-model",
        max_output_tokens=512,
    )

    output = await client.generate(
        StructuredOutputRequest(
            system_prompt="Return valid JSON only.",
            user_prompt="customer content",
            schema_name="intent_v1",
            json_schema='{"type":"object"}',
        )
    )

    assert output == '{"intent":"unknown"}'
    assert service.project_id == project_id
    assert service.request.provider_id == provider_id
    assert service.request.auto_execute_tools is False
    assert service.request.tools is None
    assert service.request.response_format.type == "json_object"
    assert "JSON Schema" in service.request.messages[0].content
    assert service.request.messages[1].content == "customer content"


@pytest.mark.asyncio
async def test_structured_output_adapter_rejects_empty_response() -> None:
    class EmptyChatService(FakeChatService):
        async def create_completion(self, request, project_id):
            return ChatCompletionResponse(
                model=request.model,
                choices=[
                    Choice(
                        index=0,
                        message=ChoiceMessage(content=None),
                        finish_reason="stop",
                    )
                ],
            )

    client = ChatServiceStructuredOutputClient(
        chat_service=EmptyChatService(),
        project_id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
        model="test-model",
    )

    with pytest.raises(RuntimeError, match="empty content"):
        await client.generate(
            StructuredOutputRequest(
                system_prompt="Return JSON.",
                user_prompt="text",
                schema_name="intent_v1",
                json_schema='{"type":"object"}',
            )
        )
