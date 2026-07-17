from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.entities import NormalizedMessage, StreamEvent
from app.domain.services import dispatcher


class FakeSession:
    async def scalar(self, _: object) -> object:
        return SimpleNamespace(type="wecom", config={})


class FakeTgoApiClient:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def chat_completion(self, request: object):
        self.requests.append(request)

        async def empty_frames():
            if False:
                yield b""

        return empty_frames()


class FakeSSEManager:
    def __init__(self, events: list[StreamEvent]) -> None:
        self.events = events

    async def stream_events(self, _: object):
        for event in self.events:
            yield event


class FakeAdapter:
    supports_stream = False

    def __init__(self) -> None:
        self.final_payloads: list[dict[str, object]] = []

    async def send_incremental(self, _: StreamEvent) -> None:
        raise AssertionError("WeCom must not stream incremental events")

    async def send_final(self, content: dict[str, object]) -> None:
        self.final_payloads.append(content)


def make_message() -> NormalizedMessage:
    return NormalizedMessage(
        source="wecom",
        from_uid="external-user",
        content="question",
        platform_api_key="platform-key",
        platform_type="wecom",
        platform_id="00000000-0000-0000-0000-000000000001",
        extra={
            "msg_type": "text",
            "message_id": "customer-message",
            "wecom": {
                "is_from_colleague": False,
                "open_kfid": "wk-test",
                "external_userid": "external-user",
            },
        },
    )


@pytest.mark.asyncio
async def test_current_agent_stream_contract_produces_one_complete_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeAdapter()
    client = FakeTgoApiClient()
    events = [
        StreamEvent(
            event="agent_content_chunk",
            payload={
                "event_type": "agent_content_chunk",
                "data": {
                    "event_type": "agent_content_chunk",
                    "data": {"content_chunk": "你"},
                },
            },
        ),
        StreamEvent(
            event="agent_content_chunk",
            payload={
                "event_type": "agent_content_chunk",
                "data": {
                    "event_type": "agent_content_chunk",
                    "data": {"content_chunk": "好"},
                },
            },
        ),
        StreamEvent(
            event="agent_response_complete",
            payload={"event_type": "agent_response_complete", "data": {}},
        ),
        StreamEvent(
            event="workflow_completed",
            payload={"event_type": "workflow_completed", "data": {}},
        ),
    ]

    async def fake_select_adapter(*_: object, **__: object) -> FakeAdapter:
        return adapter

    monkeypatch.setattr(dispatcher, "select_adapter_for_target", fake_select_adapter)

    reply = await dispatcher.process_message(
        msg=make_message(),
        db=FakeSession(),
        tgo_api_client=client,
        sse_manager=FakeSSEManager(events),
    )

    assert reply == "你好"
    assert adapter.final_payloads == [{"text": "你好"}]
    assert client.requests[0].msg_type == 1


@pytest.mark.asyncio
async def test_failed_stream_never_sends_partial_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeAdapter()
    events = [
        StreamEvent(
            event="agent_content_chunk",
            payload={
                "event_type": "agent_content_chunk",
                "data": {"data": {"content_chunk": "partial"}},
            },
        ),
        StreamEvent(
            event="workflow_failed",
            payload={"event_type": "workflow_failed", "data": {"error": "model failed"}},
        ),
    ]

    async def fake_select_adapter(*_: object, **__: object) -> FakeAdapter:
        return adapter

    monkeypatch.setattr(dispatcher, "select_adapter_for_target", fake_select_adapter)

    with pytest.raises(RuntimeError, match="workflow_failed"):
        await dispatcher.process_message(
            msg=make_message(),
            db=FakeSession(),
            tgo_api_client=FakeTgoApiClient(),
            sse_manager=FakeSSEManager(events),
        )

    assert adapter.final_payloads == []


@pytest.mark.asyncio
async def test_agent_completion_with_success_false_never_sends_partial_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeAdapter()
    events = [
        StreamEvent(
            event="agent_content_chunk",
            payload={
                "event_type": "agent_content_chunk",
                "data": {"data": {"content_chunk": "partial"}},
            },
        ),
        StreamEvent(
            event="agent_response_complete",
            payload={
                "event_type": "agent_response_complete",
                "data": {
                    "data": {
                        "success": False,
                        "error": "model failed",
                    }
                },
            },
        ),
        StreamEvent(
            event="workflow_completed",
            payload={"event_type": "workflow_completed", "data": {}},
        ),
    ]

    async def fake_select_adapter(*_: object, **__: object) -> FakeAdapter:
        return adapter

    monkeypatch.setattr(dispatcher, "select_adapter_for_target", fake_select_adapter)

    with pytest.raises(RuntimeError, match="model failed"):
        await dispatcher.process_message(
            msg=make_message(),
            db=FakeSession(),
            tgo_api_client=FakeTgoApiClient(),
            sse_manager=FakeSSEManager(events),
        )

    assert adapter.final_payloads == []


@pytest.mark.asyncio
async def test_agent_final_content_is_used_when_no_chunks_are_emitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeAdapter()
    events = [
        StreamEvent(
            event="agent_response_complete",
            payload={
                "event_type": "agent_response_complete",
                "data": {
                    "data": {
                        "success": True,
                        "final_content": "final reply",
                    }
                },
            },
        ),
        StreamEvent(
            event="workflow_completed",
            payload={"event_type": "workflow_completed", "data": {}},
        ),
    ]

    async def fake_select_adapter(*_: object, **__: object) -> FakeAdapter:
        return adapter

    monkeypatch.setattr(dispatcher, "select_adapter_for_target", fake_select_adapter)

    reply = await dispatcher.process_message(
        msg=make_message(),
        db=FakeSession(),
        tgo_api_client=FakeTgoApiClient(),
        sse_manager=FakeSSEManager(events),
    )

    assert reply == "final reply"
    assert adapter.final_payloads == [{"text": "final reply"}]


@pytest.mark.asyncio
async def test_team_intermediate_content_is_not_sent_to_customer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeAdapter()
    events = [
        StreamEvent(
            event="team_run_content",
            payload={
                "event_type": "team_run_content",
                "data": {"content": "internal reasoning", "is_intermediate": True},
            },
        ),
        StreamEvent(
            event="agent_content_chunk",
            payload={
                "event_type": "agent_content_chunk",
                "data": {"data": {"content_chunk": "customer reply"}},
            },
        ),
        StreamEvent(
            event="workflow_completed",
            payload={"event_type": "workflow_completed", "data": {}},
        ),
    ]

    async def fake_select_adapter(*_: object, **__: object) -> FakeAdapter:
        return adapter

    monkeypatch.setattr(dispatcher, "select_adapter_for_target", fake_select_adapter)

    reply = await dispatcher.process_message(
        msg=make_message(),
        db=FakeSession(),
        tgo_api_client=FakeTgoApiClient(),
        sse_manager=FakeSSEManager(events),
    )

    assert reply == "customer reply"
    assert adapter.final_payloads == [{"text": "customer reply"}]
