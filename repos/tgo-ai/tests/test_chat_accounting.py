"""Provider accounting preserves output and marks interrupted/error streams."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse, Usage
from app.services import chat_accounting
from app.services.model_usage import UsageTracker
from app.services.quota_authorization import metered_execution


@pytest.fixture
def inventory(monkeypatch):
    entries = []
    token = metered_execution.set(False)

    @asynccontextmanager
    async def track(project_id, model, purpose):
        tracker = UsageTracker()
        entries.append(tracker)
        yield tracker

    monkeypatch.setattr(chat_accounting, "track_model_usage", track)
    try:
        yield entries
    finally:
        metered_execution.reset(token)


def inputs():
    return ChatCompletionRequest(
        provider_id=uuid4(),
        model="synthetic",
        messages=[{"role": "user", "content": "hello"}],
    ), SimpleNamespace(project_id=uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize("measured", [True, False])
async def test_completion_preserves_missing_or_actual_provider_usage(
    inventory, measured
):
    expected = ChatCompletionResponse(
        model="synthetic",
        choices=[],
        usage=Usage(prompt_tokens=17, completion_tokens=9, total_tokens=26)
        if measured
        else None,
    )

    @chat_accounting.account_completion
    async def completion(service, request, provider):
        return expected

    request, provider = inputs()
    assert await completion(None, request, provider) is expected
    assert inventory[0].input_tokens == (17 if measured else None)
    assert inventory[0].output_tokens == (9 if measured else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [True, False])
async def test_stream_bytes_unchanged_and_no_fake_usage(inventory, complete):
    chunks = ['data: {"choices":[{"delta":{"content":"您好"}}]}\n\n']
    chunks.append(
        "data: [DONE]\n\n" if complete else 'data: {"error":{"message":"failed"}}\n\n'
    )

    @chat_accounting.account_stream
    async def stream(service, request, provider):
        for chunk in chunks:
            yield chunk

    request, provider = inputs()
    assert [chunk async for chunk in stream(None, request, provider)] == chunks
    assert inventory[0].status == ("succeeded" if complete else "failed")
    assert inventory[0].input_tokens is None
