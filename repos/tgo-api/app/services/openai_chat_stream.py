"""Map published reply events to complete, private compatibility streams."""

from collections.abc import AsyncGenerator
from contextlib import aclosing
import json
from typing import TypeAlias

from pydantic import JsonValue

from app.core.logging import get_logger
from app.schemas.chat import (
    OpenAIChatCompletionChunk,
    OpenAIChatCompletionChunkChoice,
    OpenAIChatCompletionDelta,
)
from app.utils.const import PUBLIC_AI_FAILURE_MESSAGE

JsonObject: TypeAlias = dict[str, JsonValue]
logger = get_logger("services.openai_chat_stream")


class _InvalidReplyStream(ValueError):
    def __init__(self) -> None:
        super().__init__("Reply stream did not complete with a valid answer")


def _payload(event: JsonObject) -> JsonObject:
    data = event.get("data")
    if not isinstance(data, dict):
        raise _InvalidReplyStream()
    # The publication path wraps the provider envelope once. Older producers
    # still send the payload directly; both shapes carry the same reply data.
    if "event_type" in data and "data" in data:
        if data["event_type"] != event.get("event_type"):
            raise _InvalidReplyStream()
        inner = data["data"]
        if not isinstance(inner, dict):
            raise _InvalidReplyStream()
        return inner
    return data


def _content(data: JsonObject) -> str:
    value = data.get("content_chunk")
    if value is not None and not isinstance(value, str):
        raise _InvalidReplyStream()
    if value:
        return value
    fallback = data.get("content")
    if fallback is not None and not isinstance(fallback, str):
        raise _InvalidReplyStream()
    return fallback or ""


def _frame(
    completion_id: str,
    created: int,
    model: str,
    *,
    text: str | None = None,
    finished: bool = False,
) -> str:
    # Text is already checked above. BaseSchema strips string whitespace;
    # preserving validated deltas here keeps word boundaries and newlines.
    delta = OpenAIChatCompletionDelta.model_construct(role=None, content=text)
    chunk = OpenAIChatCompletionChunk(
        id=completion_id,
        created=created,
        model=model,
        choices=[
            OpenAIChatCompletionChunkChoice(
                index=0,
                delta=delta,
                finish_reason="stop" if finished else None,
            )
        ],
    )
    return f"data: {chunk.model_dump_json()}\n\n"


async def openai_chat_stream(
    events: AsyncGenerator[JsonObject, None],
    *,
    completion_id: str,
    created: int,
    model: str,
) -> AsyncGenerator[str, None]:
    """Finish only after the producer and its cleanup have completed.

    Cancellation propagates; disconnecting consumers close the source rather
    than creating another reply or reporting a successful completion.
    """
    completed = False
    has_content = False
    try:
        async with aclosing(events):
            async for event in events:
                if not isinstance(event, dict) or not isinstance(
                    event.get("event_type"), str
                ):
                    raise _InvalidReplyStream()
                kind = event["event_type"]
                if kind in {
                    "workflow_failed",
                    "agent_run_failed",
                    "agent_response_error",
                    "error",
                }:
                    raise _InvalidReplyStream()
                if kind == "agent_content_chunk":
                    if completed:
                        raise _InvalidReplyStream()
                    text = _content(_payload(event))
                    if text:
                        has_content = True
                        yield _frame(completion_id, created, model, text=text)
                elif kind in {"workflow_completed", "agent_response_complete"}:
                    data = _payload(event)
                    success = data.get("success")
                    if success is False or (
                        success is not None and not isinstance(success, bool)
                    ):
                        raise _InvalidReplyStream()
                    final = data.get("final_content")
                    if final is not None and not isinstance(final, str):
                        raise _InvalidReplyStream()
                    if not has_content and final:
                        has_content = True
                        yield _frame(completion_id, created, model, text=final)
                    if not has_content:
                        raise _InvalidReplyStream()
                    completed = True
        if not completed:
            raise _InvalidReplyStream()
    except Exception as exc:
        logger.warning(
            "Compatibility reply stream failed",
            extra={"error_type": type(exc).__name__},
        )
        error = {
            "error": {
                "message": PUBLIC_AI_FAILURE_MESSAGE,
                "type": "server_error",
            }
        }
        yield f"data: {json.dumps(error)}\n\n"
    else:
        yield _frame(completion_id, created, model, finished=True)
    yield "data: [DONE]\n\n"
