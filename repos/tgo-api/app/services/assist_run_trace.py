"""Collect AI response provenance only while generating an assist draft."""

import hashlib
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

from app.schemas.assist_trace import AssistRunTrace, AssistTracePhase

_current_traces: ContextVar[list[AssistRunTrace] | None] = ContextVar(
    "assist_run_traces", default=None
)


@contextmanager
def capture_assist_run_traces() -> Iterator[list[AssistRunTrace]]:
    traces: list[AssistRunTrace] = []
    token = _current_traces.set(traces)
    try:
        yield traces
    finally:
        _current_traces.reset(token)


def record_assist_run_trace(
    phase: AssistTracePhase | None,
    response: Mapping[str, object],
    *,
    system_message: str | None = None,
    template_text: str | None = None,
    requested_agent_id: str | None = None,
) -> None:
    traces = _current_traces.get()
    if traces is None or phase is None:
        return
    metadata = response.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    raw_model = metadata.get("model_name")
    model_name = (
        raw_model
        if isinstance(raw_model, str) and 0 < len(raw_model) <= 200
        else None
    )
    raw_prompt_hash = metadata.get("configured_prompt_sha256")
    prompt_hash = (
        raw_prompt_hash
        if isinstance(raw_prompt_hash, str)
        and re.fullmatch(r"[0-9a-f]{64}", raw_prompt_hash)
        else None
    )
    try:
        parsed_agent_id = (
            UUID(requested_agent_id) if requested_agent_id else None
        )
    except (TypeError, ValueError):
        parsed_agent_id = None
    traces.append(
        AssistRunTrace(
            phase=phase,
            model_name=model_name,
            configured_prompt_sha256=prompt_hash,
            api_template_sha256=(
                hashlib.sha256(template_text.encode("utf-8")).hexdigest()
                if template_text is not None else None
            ),
            requested_agent_id=parsed_agent_id,
            request_system_prompt_sha256=(
                hashlib.sha256(system_message.encode("utf-8")).hexdigest()
                if system_message is not None else None
            ),
        )
    )
