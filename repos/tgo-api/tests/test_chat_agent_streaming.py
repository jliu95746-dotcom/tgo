"""Tests for single-agent chat streaming behavior."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

import app.services.ai_client as ai_client_module
import app.services.chat_service as chat_service


@pytest.mark.asyncio
async def test_forward_ai_event_to_wukongim_uses_agent_content_chunk(
    monkeypatch,
) -> None:
    """Agent chunk events should map to WuKongIM stream deltas."""

    sent_events: list[dict[str, object]] = []
    monkeypatch.setattr(
        chat_service.wukongim_client,
        "send_stream_event",
        AsyncMock(side_effect=lambda **kwargs: sent_events.append(kwargs)),
    )

    chunk = await chat_service.forward_ai_event_to_wukongim(
        event_type="agent_content_chunk",
        event_data={"data": {"content_chunk": "hello"}},
        channel_id="channel-1",
        channel_type=1,
        client_msg_no="msg-1",
        from_uid="agent-1-agent",
    )

    assert chunk == "hello"
    assert sent_events[0]["event_type"] == "stream.delta"


@pytest.mark.asyncio
async def test_forward_ai_event_to_wukongim_finishes_on_agent_response_complete(
    monkeypatch,
) -> None:
    """Agent completion events should close and finish the stream."""

    sent_events: list[dict[str, object]] = []
    monkeypatch.setattr(
        chat_service.wukongim_client,
        "send_stream_event",
        AsyncMock(side_effect=lambda **kwargs: sent_events.append(kwargs)),
    )

    await chat_service.forward_ai_event_to_wukongim(
        event_type="agent_response_complete",
        event_data={"data": {}},
        channel_id="channel-1",
        channel_type=1,
        client_msg_no="msg-1",
        from_uid="agent-1-agent",
    )

    assert [event["event_type"] for event in sent_events] == [
        "stream.close",
        "stream.finish",
    ]


@pytest.mark.asyncio
async def test_completion_forwards_final_content_when_provider_sent_no_chunks(
    monkeypatch,
) -> None:
    """A completed non-streaming provider response must still reach WuKongIM."""

    sent_events: list[dict[str, object]] = []
    monkeypatch.setattr(
        chat_service.wukongim_client,
        "send_stream_event",
        AsyncMock(side_effect=lambda **kwargs: sent_events.append(kwargs)),
    )

    content = await chat_service.forward_ai_event_to_wukongim(
        event_type="agent_response_complete",
        event_data={
            "data": {
                "final_content": "完整回答",
                "total_chunks": 0,
            }
        },
        channel_id="channel-1",
        channel_type=1,
        client_msg_no="msg-1",
        from_uid="agent-1-agent",
    )

    assert content == "完整回答"
    assert [event["event_type"] for event in sent_events] == [
        "stream.delta",
        "stream.close",
        "stream.finish",
    ]
    assert sent_events[0]["payload"] == {
        "kind": "text",
        "delta": "完整回答",
    }


@pytest.mark.asyncio
async def test_forward_ai_event_to_wukongim_reports_workflow_failed(
    monkeypatch,
) -> None:
    """Workflow failures should map to WuKongIM stream errors."""

    sent_events: list[dict[str, object]] = []
    monkeypatch.setattr(
        chat_service.wukongim_client,
        "send_stream_event",
        AsyncMock(side_effect=lambda **kwargs: sent_events.append(kwargs)),
    )

    await chat_service.forward_ai_event_to_wukongim(
        event_type="workflow_failed",
        event_data={"data": {"error": "boom"}},
        channel_id="channel-1",
        channel_type=1,
        client_msg_no="msg-1",
        from_uid="agent-1-agent",
    )

    assert [event["event_type"] for event in sent_events] == [
        "stream.error",
        "stream.close",
        "stream.finish",
    ]


@pytest.mark.asyncio
async def test_run_background_ai_interaction_sets_started_event_on_agent_execution_started(
    monkeypatch,
) -> None:
    """Background AI runs should report startup on the new agent event."""

    async def fake_process(*_args, **_kwargs):
        yield {"event_type": "agent_execution_started", "data": {"run_id": "run-1"}}
        yield {"event_type": "workflow_completed", "data": {}}

    monkeypatch.setattr(chat_service, "process_ai_stream_to_wukongim", fake_process)

    started_event = asyncio.Event()

    await chat_service.run_background_ai_interaction(
        project_id="project-1",
        user_id="user-1",
        message="hello",
        channel_id="channel-1",
        channel_type=1,
        client_msg_no="msg-1",
        from_uid="agent-1-agent",
        session_id="session-1",
        agent_id="agent-1",
        started_event=started_event,
    )

    assert started_event.is_set()


@pytest.mark.asyncio
async def test_process_ai_stream_sends_thinking_anchor_before_ai_start(
    monkeypatch,
) -> None:
    """The visitor should see immediate feedback while agent setup is running."""

    calls: list[str] = []

    async def fake_forward(*, event_type: str, **_kwargs):
        calls.append(event_type)
        return None

    async def fake_stream(**_kwargs):
        calls.append("ai_client_started")
        yield "agent_execution_started", {
            "event_type": "agent_execution_started",
            "data": {},
        }
        yield "agent_response_complete", {
            "event_type": "agent_response_complete",
            "data": {"final_content": "ok", "total_chunks": 0},
        }

    monkeypatch.setattr(
        chat_service,
        "forward_ai_event_to_wukongim",
        fake_forward,
    )
    monkeypatch.setattr(
        chat_service.ai_client,
        "run_supervisor_agent_stream",
        fake_stream,
    )

    events = [
        event
        async for event in chat_service.process_ai_stream_to_wukongim(
            project_id="project-1",
            user_id="user-1",
            message="hello",
            channel_id="channel-1",
            channel_type=1,
            client_msg_no="msg-1",
            from_uid="agent-1-agent",
        )
    ]

    assert calls[0] == "agent_execution_started"
    assert calls[1] == "ai_client_started"
    assert calls.count("agent_execution_started") == 1
    assert events[-1]["event_type"] == "agent_response_complete"


@pytest.mark.asyncio
async def test_non_stream_response_batches_provider_chunks_for_wukongim(
    monkeypatch,
) -> None:
    """Provider token chunks should become one WuKongIM delta at completion."""

    forwarded: list[tuple[str, dict[str, object]]] = []

    async def fake_forward(
        *,
        event_type: str,
        event_data: dict[str, object],
        **_kwargs,
    ):
        forwarded.append((event_type, event_data))
        completion_data = event_data.get("data")
        if event_type == "agent_response_complete" and isinstance(
            completion_data, dict
        ):
            return completion_data.get("final_content")
        return None

    async def fake_stream(**_kwargs):
        yield "agent_execution_started", {
            "event_type": "agent_execution_started",
            "data": {},
        }
        yield "agent_content_chunk", {
            "event_type": "agent_content_chunk",
            "data": {"content_chunk": "hello"},
        }
        yield "agent_content_chunk", {
            "event_type": "agent_content_chunk",
            "data": {"content_chunk": " world"},
        }
        yield "agent_response_complete", {
            "event_type": "agent_response_complete",
            "data": {},
        }

    monkeypatch.setattr(
        chat_service,
        "forward_ai_event_to_wukongim",
        fake_forward,
    )
    monkeypatch.setattr(
        chat_service.ai_client,
        "run_supervisor_agent_stream",
        fake_stream,
    )

    result = await chat_service.handle_ai_response_non_stream(
        project_id="project-1",
        visitor_id="visitor-1",
        message="hello",
        channel_id="channel-1",
        channel_type=1,
        client_msg_no="msg-1",
        from_uid="agent-1-agent",
    )

    assert result["content"] == "hello world"
    assert [event_type for event_type, _ in forwarded] == [
        "agent_execution_started",
        "agent_response_complete",
    ]
    assert forwarded[-1][1]["data"] == {
        "final_content": "hello world",
        "total_chunks": 0,
    }


@pytest.mark.asyncio
async def test_non_stream_response_discards_draft_content_before_tool_call(
    monkeypatch,
) -> None:
    """Only the post-tool answer should be persisted and sent to the visitor."""

    forwarded: list[tuple[str, dict[str, object]]] = []

    async def fake_forward(
        *,
        event_type: str,
        event_data: dict[str, object],
        **_kwargs,
    ):
        forwarded.append((event_type, event_data))
        return None

    async def fake_stream(**_kwargs):
        yield "agent_execution_started", {
            "event_type": "agent_execution_started",
            "data": {},
        }
        yield "agent_content_chunk", {
            "event_type": "agent_content_chunk",
            "data": {"content_chunk": "我先帮您查询一下。"},
        }
        yield "agent_tool_call_started", {
            "event_type": "agent_tool_call_started",
            "data": {"tool_name": "express_service"},
        }
        yield "agent_tool_call_completed", {
            "event_type": "agent_tool_call_completed",
            "data": {"tool_name": "express_service"},
        }
        yield "agent_content_chunk", {
            "event_type": "agent_content_chunk",
            "data": {"content_chunk": "快件正在运输中。"},
        }
        yield "agent_response_complete", {
            "event_type": "agent_response_complete",
            "data": {"final_content": "我先帮您查询一下。快件正在运输中。"},
        }

    monkeypatch.setattr(
        chat_service,
        "forward_ai_event_to_wukongim",
        fake_forward,
    )
    monkeypatch.setattr(
        chat_service.ai_client,
        "run_supervisor_agent_stream",
        fake_stream,
    )

    result = await chat_service.handle_ai_response_non_stream(
        project_id="project-1",
        visitor_id="visitor-1",
        message="查询物流",
        channel_id="channel-1",
        channel_type=1,
        client_msg_no="msg-1",
        from_uid="agent-1-agent",
    )

    assert result["content"] == "快件正在运输中。"
    assert forwarded[-1][1]["data"] == {
        "final_content": "快件正在运输中。",
        "total_chunks": 0,
    }


@pytest.mark.asyncio
async def test_scheduled_background_run_is_retained_until_completion(
    monkeypatch,
) -> None:
    """Fire-and-forget AI runs need a strong reference until they finish."""

    release = asyncio.Event()

    async def fake_run(**_kwargs):
        await release.wait()

    monkeypatch.setattr(
        chat_service,
        "run_background_ai_interaction",
        fake_run,
    )

    task = chat_service.schedule_background_ai_interaction(
        project_id="project-1",
        user_id="user-1",
        message="hello",
        channel_id="channel-1",
        channel_type=1,
        client_msg_no="msg-1",
        from_uid="agent-1-agent",
    )

    assert task in chat_service.background_ai_tasks
    release.set()
    await task
    await asyncio.sleep(0)
    assert task not in chat_service.background_ai_tasks


@pytest.mark.asyncio
async def test_run_supervisor_agent_stream_omits_legacy_team_selectors(
    monkeypatch,
) -> None:
    """The downstream streaming payload should never include team selectors."""

    captured: dict[str, object] = {}

    class _FakeStreamResponse:
        status_code = 200

        async def __aenter__(self) -> "_FakeStreamResponse":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def aiter_lines(self):
            if False:
                yield ""

    class _FakeAsyncClient:
        def __init__(self, timeout=None, trust_env=True) -> None:
            self.timeout = timeout
            captured["trust_env"] = trust_env

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        def stream(self, method: str, url: str, headers=None, json=None, params=None):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            captured["params"] = params
            return _FakeStreamResponse()

    monkeypatch.setattr(ai_client_module.httpx, "AsyncClient", _FakeAsyncClient)

    client = ai_client_module.AIServiceClient()
    events = [
        event
        async for event in client.run_supervisor_agent_stream(
            project_id="project-1",
            agent_id="agent-1",
            user_id="user-1",
            message="hello",
            session_id="session-1",
            knowledge_channel="internal",
            enable_memory=True,
            system_message="system",
            expected_output="output",
            excluded_tool_ids=["11111111-1111-1111-1111-111111111111"],
        )
    ]

    assert events == []
    payload = captured["json"]
    assert payload["agent_id"] == "agent-1"
    assert payload["knowledge_channel"] == "internal"
    assert payload["ui_mode"] == "text"
    assert payload["excluded_tool_ids"] == [
        "11111111-1111-1111-1111-111111111111"
    ]
    assert captured["trust_env"] is False
    assert "team_id" not in payload
    assert "agent_ids" not in payload
    assert "config" not in payload
