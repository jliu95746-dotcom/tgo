"""A completed provider result cannot bypass an exact-run publication fence."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from agno.agent import RunCompletedEvent

from app.runtime.supervisor.application import service as module
from app.runtime.supervisor.application.service import SupervisorRuntimeService
from app.schemas.agent_run import SupervisorRunRequest
from app.streaming.event_emitter import StreamingEventEmitter
from tests.test_agent_execution_scope import reference
from tests.test_supervisor_agent_runner import _build_context


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_model_completion_after_policy_change_never_becomes_a_successful_final_response(monkeypatch, stream):
    ref = reference()
    context = _build_context().model_copy(update={"project_id": str(ref.project_id), "execution_reference": ref})
    context.agent = context.agent.model_copy(update={"id": ref.agent_id, "project_id": str(ref.project_id)})
    runtime = SupervisorRuntimeService(session_factory=Mock(), tools_runtime_service=Mock(_settings=None))
    runtime._prepare_context = AsyncMock(return_value=(context, str(ref.agent_id)))
    runtime._execution_reader = AsyncMock(return_value=ref)

    async def content():
        runtime._execution_reader.return_value = ref.model_copy(update={"policy_revision": "c" * 64})
        yield RunCompletedEvent(content="must not release")

    async def complete(*args, **kwargs):
        runtime._execution_reader.return_value = ref.model_copy(update={"policy_revision": "c" * 64})
        return SimpleNamespace(content="must not release", tools=[], status="COMPLETED")

    agent = SimpleNamespace(arun=(lambda *args, **kwargs: content()) if stream else complete)
    runtime._agent_builder.build_agent = AsyncMock(return_value=SimpleNamespace(agent=agent))
    monkeypatch.setattr(module, "get_event_emitter", lambda request, correlation: StreamingEventEmitter(request, correlation))
    payload = SupervisorRunRequest(message="test")
    if stream:
        response = await runtime.stream(payload, ref.project_id, http_request=SimpleNamespace(is_disconnected=AsyncMock(return_value=False)))
        async with asyncio.timeout(3):
            chunks = [chunk async for chunk in response.body_iterator]
        body = "".join(chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks)
        assert "workflow_failed" in body and "agent_response_complete" not in body and "workflow_completed" not in body
        assert "must not release" not in body
    else:
        result = await runtime.run(payload, ref.project_id)
        assert not result.success and result.content == "" and result.result is None
        assert "must not release" not in result.model_dump_json()
