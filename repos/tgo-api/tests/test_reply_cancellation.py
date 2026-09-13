"""Stop through the real chat pipeline, without contacting real customers."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services import ai_reply_control as control
from app.services import chat_service
from app.services.run_registry import InMemoryRunRegistry, RegistryUnavailable

pytestmark = pytest.mark.asyncio


@pytest.fixture
def pipeline(monkeypatch):
    registry = InMemoryRunRegistry()
    monkeypatch.setattr(control, "run_registry", registry)
    monkeypatch.setattr(control, "POLL_SECONDS", 0.005)
    monkeypatch.setattr(control, "PHASE_WAIT_SECONDS", 0.05)
    forward = AsyncMock()
    monkeypatch.setattr(chat_service, "forward_ai_event_to_wukongim", forward)
    monkeypatch.setattr(
        chat_service, "recent_customer_messages", AsyncMock(return_value=[])
    )
    stop = AsyncMock(return_value={"run_id": "owned-run", "cancelled": True})
    monkeypatch.setattr(chat_service.ai_client, "cancel_supervisor_run", stop)
    arguments = dict(
        project_id=str(uuid4()),
        user_id=str(uuid4()),
        message="绿色有吗？",
        channel_id=f"{uuid4()}-vtr",
        channel_type=251,
        client_msg_no=uuid4().hex,
        from_uid="owned-agent",
    )

    async def confirm_stopped(*args):
        item = await registry.get(arguments["project_id"], arguments["client_msg_no"])
        if item is not None and item.phase is not None:
            await registry.end_phase(item.phase)
        return {"run_id": "owned-run", "cancelled": True}

    stop.side_effect = confirm_stopped
    return registry, arguments, forward, stop


@pytest.mark.parametrize("phase", ["lookup", "rewrite"])
@pytest.mark.parametrize("streaming", [True, False])
async def test_stop_prevents_publication_in_both_reply_phases(
    monkeypatch, pipeline, phase, streaming
):
    registry, arguments, forward, stop = pipeline
    waiting, closed = asyncio.Event(), asyncio.Event()

    async def events(**kwargs):
        try:
            yield "event", {
                "event_type": "agent_execution_started",
                "data": {"execution_id": "owned-run"},
            }
            if phase == "lookup":
                waiting.set()
                await asyncio.Event().wait()
            yield "event", {
                "event_type": "agent_response_complete",
                "data": {"success": True, "final_content": "已确认没有绿色。"},
            }
            yield "event", {"event_type": "workflow_completed", "data": {}}
        finally:
            closed.set()

    async def rewrite(*args, **kwargs):
        waiting.set()
        await asyncio.Event().wait()
        return "不得发送"

    monkeypatch.setattr(chat_service.ai_client, "run_supervisor_agent_stream", events)
    monkeypatch.setattr(chat_service, "rewrite_assist_draft", rewrite)

    async def consume():
        if streaming:
            return [
                item
                async for item in chat_service.process_ai_stream_to_wukongim(
                    **arguments
                )
            ]
        args = {**arguments, "visitor_id": arguments["user_id"]}
        args.pop("user_id")
        return await chat_service.handle_ai_response_non_stream(**args)

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(waiting.wait(), 1)
        item = await registry.get(arguments["project_id"], arguments["client_msg_no"])
        assert item is not None
        await registry.request_cancel(item)
        result = await asyncio.wait_for(task, 1)
        assert "不得发送" not in str(result)
        assert closed.is_set()
        assert (
            await registry.get(item.project_id, item.client_msg_no)
        ).status == "cancelled"
        sent_types = [call.kwargs["event_type"] for call in forward.await_args_list]
        assert sent_types == ["agent_execution_started", "workflow_failed"]
        assert stop.await_count == int(phase == "lookup")
        if not streaming:
            assert result["success"] is False
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_publication_already_started_cannot_claim_to_cancel(
    monkeypatch, pipeline
):
    registry, arguments, forward, stop = pipeline
    publishing, release = asyncio.Event(), asyncio.Event()

    async def events(**kwargs):
        yield "agent_response_complete", {
            "data": {"success": True, "final_content": "有绿色。"}
        }
        yield "workflow_completed", {"data": {}}

    async def send(**kwargs):
        if kwargs["event_type"] == "workflow_completed":
            publishing.set()
            await release.wait()

    monkeypatch.setattr(chat_service.ai_client, "run_supervisor_agent_stream", events)
    monkeypatch.setattr(
        chat_service, "rewrite_assist_draft", AsyncMock(return_value="有绿色。")
    )
    forward.side_effect = send

    async def consume():
        return [
            item
            async for item in chat_service.process_ai_stream_to_wukongim(**arguments)
        ]

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(publishing.wait(), 1)
        item = await registry.get(arguments["project_id"], arguments["client_msg_no"])
        assert (await registry.request_cancel(item)).status == "publishing"
        release.set()
        await asyncio.wait_for(task, 1)
        assert (
            await registry.get(item.project_id, item.client_msg_no)
        ).status == "completed"
        stop.assert_not_awaited()
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_lost_control_storage_cannot_publish(monkeypatch, pipeline):
    registry, arguments, forward, stop = pipeline
    waiting = asyncio.Event()

    async def events(**kwargs):
        waiting.set()
        await asyncio.Event().wait()
        yield "workflow_completed", {"data": {}}

    monkeypatch.setattr(chat_service.ai_client, "run_supervisor_agent_stream", events)
    monkeypatch.setattr(
        registry, "heartbeat", AsyncMock(side_effect=RegistryUnavailable())
    )

    async def consume():
        return [
            item
            async for item in chat_service.process_ai_stream_to_wukongim(**arguments)
        ]

    result = await asyncio.wait_for(consume(), 1)
    assert not any(item["event_type"] == "workflow_completed" for item in result)
    assert not any(
        call.kwargs["event_type"] == "workflow_completed"
        for call in forward.await_args_list
    )


@pytest.mark.parametrize("known_id", [False, True])
@pytest.mark.parametrize("signal_accepted", [False, True])
async def test_unknown_or_rejected_upstream_stop_is_not_confirmed(
    monkeypatch, pipeline, known_id, signal_accepted
):
    registry, arguments, forward, stop = pipeline
    stop.return_value = {"run_id": "owned-run", "cancelled": signal_accepted}
    stop.side_effect = None
    waiting = asyncio.Event()

    async def events(**kwargs):
        if known_id:
            yield "agent_execution_started", {"data": {"execution_id": "owned-run"}}
        waiting.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(chat_service.ai_client, "run_supervisor_agent_stream", events)

    async def consume():
        return [
            item
            async for item in chat_service.process_ai_stream_to_wukongim(**arguments)
        ]

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(waiting.wait(), 1)
        item = await registry.get(arguments["project_id"], arguments["client_msg_no"])
        await registry.request_cancel(item)
        await asyncio.wait_for(task, 1)
        result = await registry.get(item.project_id, item.client_msg_no)
        assert result.status == "failed"
        assert result.failure_reason == "upstream_stop_unconfirmed"
        assert not any(
            call.kwargs["event_type"] == "workflow_completed"
            for call in forward.await_args_list
        )
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_closing_consumer_awaits_owned_producer_cleanup(monkeypatch, pipeline):
    registry, arguments, forward, stop = pipeline
    waiting, closed = asyncio.Event(), asyncio.Event()

    async def events(**kwargs):
        try:
            yield "agent_execution_started", {"data": {"execution_id": "owned-run"}}
            waiting.set()
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(chat_service.ai_client, "run_supervisor_agent_stream", events)
    source = chat_service.process_ai_stream_to_wukongim(**arguments)
    await source.__anext__()
    await asyncio.wait_for(waiting.wait(), 1)
    await asyncio.wait_for(source.aclose(), 1)
    assert closed.is_set()
    stop.assert_awaited_once()
    assert (
        await registry.get(arguments["project_id"], arguments["client_msg_no"])
    ).status == "failed"


@pytest.mark.parametrize("stop_behavior", ["slow", "cancelled"])
@pytest.mark.parametrize("phase_confirmed", [False, True])
async def test_interrupted_stop_signal_still_records_terminal_status(
    monkeypatch, pipeline, stop_behavior, phase_confirmed
):
    registry, arguments, forward, stop = pipeline
    waiting = asyncio.Event()

    async def events(**kwargs):
        yield "agent_execution_started", {"data": {"execution_id": "owned-run"}}
        waiting.set()
        await asyncio.Event().wait()

    async def interrupted_stop(*args):
        if phase_confirmed:
            item = await registry.get(
                arguments["project_id"], arguments["client_msg_no"]
            )
            await registry.end_phase(item.phase)
        if stop_behavior == "cancelled":
            raise asyncio.CancelledError()
        await asyncio.Event().wait()

    stop.side_effect = interrupted_stop
    monkeypatch.setattr(chat_service.ai_client, "run_supervisor_agent_stream", events)

    async def consume():
        return [
            event
            async for event in chat_service.process_ai_stream_to_wukongim(**arguments)
        ]

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(waiting.wait(), 1)
        item = await registry.get(arguments["project_id"], arguments["client_msg_no"])
        await registry.request_cancel(item)
        await asyncio.wait_for(task, 2)
        result = await registry.get(item.project_id, item.client_msg_no)
        assert result.status == ("cancelled" if phase_confirmed else "failed")
        assert result.failure_reason == (
            None if phase_confirmed else "upstream_stop_unconfirmed"
        )
        assert forward.await_args_list[-1].kwargs["event_type"] == "workflow_failed"
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("phase", ["rewrite", "audit"])
async def test_expression_request_disconnect_is_not_a_termination_receipt(
    monkeypatch, pipeline, phase
):
    registry, arguments, forward, stop = pipeline
    waiting, closed = asyncio.Event(), asyncio.Event()
    calls = []

    async def events(**kwargs):
        assert kwargs["cancel_on_disconnect"] is True
        yield "agent_response_complete", {
            "data": {"success": True, "final_content": "这款没有绿色。"}
        }
        yield "workflow_completed", {"data": {}}

    async def expression(**kwargs):
        calls.append(kwargs)
        assert kwargs["cancel_on_disconnect"] is True
        if phase == "audit" and len(calls) == 1:
            return {"content": "这款没有绿色。"}
        waiting.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(chat_service.ai_client, "run_supervisor_agent_stream", events)
    monkeypatch.setattr(chat_service.ai_client, "run_supervisor_agent", expression)

    async def consume():
        return [
            event
            async for event in chat_service.process_ai_stream_to_wukongim(**arguments)
        ]

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(waiting.wait(), 1)
        item = await registry.get(arguments["project_id"], arguments["client_msg_no"])
        await registry.request_cancel(item)
        await asyncio.wait_for(task, 1)
        result = await registry.get(item.project_id, item.client_msg_no)
        assert closed.is_set()
        assert result.status == "failed"
        assert result.failure_reason == "upstream_stop_unconfirmed"
        assert not any(
            call.kwargs["event_type"] == "workflow_completed"
            for call in forward.await_args_list
        )
        stop.assert_not_awaited()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
