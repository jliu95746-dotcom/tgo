"""The exact run is fenced before tools and output; revocation cannot revive."""
import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from agno.tools.function import Function, FunctionCall


def reference():
    from app.schemas.agent_execution import AgentExecutionReference
    return AgentExecutionReference(project_id=uuid4(), agent_id=uuid4(), channel="web",
        configuration_fingerprint="a" * 64, policy_revision="b" * 64)


@pytest.mark.asyncio
async def test_scope_is_task_local_and_a_revoked_run_cannot_be_restored():
    from app.services.agent_execution_scope import ExecutionRevoked, current_execution_scope, track_execution_scope
    ref = reference()
    read = AsyncMock(return_value=ref)
    assert current_execution_scope() is None
    async with track_execution_scope(ref, read) as scope:
        assert current_execution_scope() is scope
        await scope.check()
        read.return_value = ref.model_copy(update={"policy_revision": "c" * 64})
        with pytest.raises(ExecutionRevoked):
            await scope.check()
        read.return_value = ref
        with pytest.raises(ExecutionRevoked):
            await scope.check()
    assert current_execution_scope() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["project_id", "agent_id", "channel", "configuration_fingerprint", "policy_revision"])
async def test_any_changed_execution_identity_stops_before_a_real_function(field):
    from app.services.agent_execution_scope import ExecutionRevoked, track_execution_scope
    ref = reference()
    value = uuid4() if field.endswith("_id") else "internal" if field == "channel" else "c" * 64
    read = AsyncMock(return_value=ref.model_copy(update={field: value}))
    invoked = []

    async def action() -> str:
        invoked.append(True)
        return "fixture result"

    async with track_execution_scope(ref, read) as scope:
        call = FunctionCall(function=Function(name="fixture", entrypoint=action, tool_hooks=[scope.tool_hook]), arguments={})
        with pytest.raises(ExecutionRevoked):
            await call.aexecute()
    assert not invoked


@pytest.mark.asyncio
async def test_a_captured_hook_cannot_execute_after_its_owner_has_finished():
    from app.services.agent_execution_scope import ExecutionRevoked, track_execution_scope
    invoked = AsyncMock(return_value="result")
    async with track_execution_scope(reference(), AsyncMock(return_value=reference())) as scope:
        captured = scope.tool_hook
    with pytest.raises(ExecutionRevoked):
        await captured(invoked, {})
    invoked.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_tool_rechecks_after_result_and_preserves_no_publish_authority():
    from app.services.agent_execution_scope import ExecutionRevoked, track_execution_scope
    ref = reference()
    read = AsyncMock(return_value=ref)
    invoked = []

    async def action() -> str:
        invoked.append(True)
        read.return_value = ref.model_copy(update={"configuration_fingerprint": "c" * 64})
        return "must not be released"

    async with track_execution_scope(ref, read) as scope:
        with pytest.raises(ExecutionRevoked):
            await scope.tool_hook(action, {})
        assert scope.revoked
    assert invoked == [True]  # The action happened; do not call it unexecuted.


@pytest.mark.asyncio
async def test_dependency_failure_poison_is_not_a_cached_authorization():
    from app.services.agent_execution_scope import ExecutionRevoked, track_execution_scope
    ref = reference()
    read = AsyncMock(side_effect=OSError("private-endpoint-token"))
    async with track_execution_scope(ref, read) as scope:
        with pytest.raises(ExecutionRevoked) as failure:
            await scope.check()
        assert "private-endpoint-token" not in str(failure.value)
        read.side_effect = None
        read.return_value = ref
        with pytest.raises(ExecutionRevoked):
            await scope.check()


@pytest.mark.asyncio
async def test_child_tool_denial_cancels_the_owned_parent_before_more_model_work():
    from app.services.agent_execution_scope import ExecutionRevoked, track_execution_scope
    ref = reference()
    activity = []

    async def parent():
        async with track_execution_scope(ref, AsyncMock(side_effect=OSError("private"))) as scope:
            try:
                await asyncio.create_task(scope.tool_hook(AsyncMock(return_value="unsafe"), {}))
                activity.append("another model request")
            except (asyncio.CancelledError, ExecutionRevoked):
                assert scope.revoked
                scope.release_owned_cancel()
    await asyncio.create_task(parent())
    assert not activity


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["before", "after"])
async def test_real_agno_loop_stops_after_revocation_without_another_model_request(when, monkeypatch):
    import json
    import httpx
    from agno.agent import Agent
    from agno.models.openai import OpenAIChat
    from app.services.agent_execution_scope import ExecutionRevoked, track_execution_scope
    ref = reference()
    read = AsyncMock(return_value=ref)
    requests, calls = [], []

    async def action() -> str:
        """Return a fixture without accessing a real business system."""
        calls.append(True)
        if when == "after":
            read.return_value = ref.model_copy(update={"policy_revision": "c" * 64})
        return "fixture"

    def model(request):
        assert request.url.host == "fixture.invalid"
        requests.append(json.loads(request.content))
        if when == "before":
            read.return_value = ref.model_copy(update={"policy_revision": "c" * 64})
        return httpx.Response(200, json={"id": "fixture", "created": 1, "model": "fixture", "object": "chat.completion",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
                "tool_calls": [{"id": "owned", "type": "function", "function": {"name": "action", "arguments": "{}"}}]}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
        chat = OpenAIChat(
            id="fixture", api_key="fixture-only",
            base_url="http://fixture.invalid/v1", http_client=client,
            max_retries=0,
        )
        # Keep SDK platform discovery outside the revocation deadline.
        monkeypatch.setattr(chat.get_async_client(), "_platform", "Windows")

        async def run():
            with pytest.raises(ExecutionRevoked):
                async with track_execution_scope(ref, read) as scope:
                    agent = Agent(
                        model=chat, tools=[action],
                        tool_hooks=[scope.tool_hook], telemetry=False,
                    )
                    await agent.arun("isolated test", stream=False)
        await asyncio.wait_for(asyncio.create_task(run()), 3)
    assert len(requests) == 1
    assert calls == ([] if when == "before" else [True])


@pytest.mark.asyncio
@pytest.mark.parametrize("sync", [False, True])
async def test_streaming_tool_does_not_run_next_step_after_revocation(sync):
    from app.services.agent_execution_scope import ExecutionRevoked, track_execution_scope
    ref = reference()
    read = AsyncMock(return_value=ref)
    calls, closed = [], []

    async def asynchronous():
        try:
            calls.append(1)
            yield "first"
            calls.append(2)
            yield "second"
        finally:
            closed.append(True)

    def synchronous():
        try:
            calls.append(1)
            yield "first"
            calls.append(2)
            yield "second"
        finally:
            closed.append(True)

    async with track_execution_scope(ref, read) as scope:
        iterator = await scope.tool_hook(synchronous if sync else asynchronous, {})
        assert await anext(iterator) == "first"
        read.return_value = ref.model_copy(update={"policy_revision": "c" * 64})
        with pytest.raises(ExecutionRevoked):
            await anext(iterator)
    assert calls == [1] and closed == [True]


@pytest.mark.asyncio
async def test_an_inflight_check_cannot_release_a_hook_after_scope_closure():
    from app.services.agent_execution_scope import ExecutionRevoked, track_execution_scope
    started, release = asyncio.Event(), asyncio.Event()
    ref = reference()

    async def read(_):
        started.set()
        await release.wait()
        return ref

    action = AsyncMock()
    async with track_execution_scope(ref, read) as scope:
        task = asyncio.create_task(scope.tool_hook(action, {}))
        await started.wait()
    release.set()
    with pytest.raises(ExecutionRevoked):
        await task
    action.assert_not_awaited()
    assert asyncio.current_task().cancelling() == 0
