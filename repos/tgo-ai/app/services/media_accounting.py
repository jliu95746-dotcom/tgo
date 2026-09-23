"""Keep media provider usage unknown when its response omits token metrics."""

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from uuid import UUID
from pydantic import BaseModel, Field, ValidationError
from app.services.model_usage import UsageTracker, track_model_usage


class _TokenUsage(BaseModel):
    prompt_tokens: int | None = Field(default=None, ge=0, le=2147483647)
    completion_tokens: int | None = Field(default=None, ge=0, le=2147483647)


class _ProviderResult(BaseModel):
    usage: _TokenUsage | None = None


def observe_media_usage(tracker: UsageTracker, raw: bytes) -> None:
    try:
        result = _ProviderResult.model_validate_json(raw)
    except ValidationError:
        return
    if result.usage is not None:
        tracker.input_tokens = result.usage.prompt_tokens
        tracker.output_tokens = result.usage.completion_tokens


@asynccontextmanager
async def media_execution(model: str) -> AsyncIterator[UsageTracker]:
    from app.services.quota_authorization import current_authorization

    authorization = current_authorization.get()
    project = authorization.project_id if authorization else UUID(int=0)
    async with track_model_usage(str(project), model, "media") as tracker:
        yield tracker
