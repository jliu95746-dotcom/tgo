"""Stream normalization preserves cancellation, cleanup, and final replies."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.openai_chat_stream import openai_chat_stream
from app.utils.const import PUBLIC_AI_FAILURE_MESSAGE

PRIVATE = "synthetic-private-stream-failure"


def adapt(source):
    return openai_chat_stream(
        source, completion_id="owned-completion", created=1234, model="tgo-ai"
    )


async def collect(events, *, cleanup_error=False):
    state = SimpleNamespace(closed=False)

    async def source():
        try:
            for event in events:
                yield event
        finally:
            state.closed = True
            if cleanup_error:
                raise RuntimeError(PRIVATE)

    output = [frame async for frame in adapt(source())]
    assert state.closed
    return output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event",
    [None, [], 7, "text", {}, {"event_type": None}, {"event_type": []}],
)
async def test_invalid_event_roots_fail_without_raw_content(event, caplog):
    output = await collect([event])
    assert output[-1] == "data: [DONE]\n\n"
    assert len(output) == 2
    assert (
        json.loads(output[0][6:])["error"]["message"]
        == PUBLIC_AI_FAILURE_MESSAGE
    )
    assert PRIVATE not in str(output) + caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,data",
    [
        (
            "agent_content_chunk",
            {"event_type": "different", "data": {"content_chunk": PRIVATE}},
        ),
        (
            "agent_content_chunk",
            {"event_type": "agent_content_chunk", "data": []},
        ),
        (
            "agent_content_chunk",
            {"content_chunk": "", "content": {"private": PRIVATE}},
        ),
        ("agent_response_complete", None),
        (
            "agent_response_complete",
            {"success": False, "final_content": PRIVATE},
        ),
        (
            "agent_response_complete",
            {"success": "true", "final_content": PRIVATE},
        ),
        ("agent_response_complete", {"success": 1, "final_content": PRIVATE}),
        ("agent_response_complete", {"final_content": [PRIVATE]}),
        ("agent_response_complete", {"final_content": {"private": PRIVATE}}),
    ],
)
async def test_invalid_nested_or_terminal_payload_never_becomes_an_answer(
    kind, data
):
    output = await collect([{"event_type": kind, "data": data}])
    assert len(output) == 2
    assert '"error"' in output[0] and output[-1] == "data: [DONE]\n\n"
    assert PRIVATE not in str(output)


@pytest.mark.asyncio
async def test_legacy_content_fallback_and_empty_chunks_are_kept():
    output = await collect(
        [
            {"event_type": "agent_execution_started", "data": {}},
            {
                "event_type": "agent_content_chunk",
                "data": {"content_chunk": ""},
            },
            {
                "event_type": "agent_content_chunk",
                "data": {"content_chunk": None, "content": " 结果 "},
            },
            {
                "event_type": "agent_response_complete",
                "data": {"success": True},
            },
        ]
    )
    assert len(output) == 3
    assert (
        json.loads(output[0][6:])["choices"][0]["delta"]["content"] == " 结果 "
    )
    assert json.loads(output[1][6:])["choices"][0]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_cleanup_failure_cannot_follow_a_success_marker(caplog):
    output = await collect(
        [
            {
                "event_type": "agent_response_complete",
                "data": {"final_content": "已生成的回答"},
            },
        ],
        cleanup_error=True,
    )
    assert len(output) == 3
    assert '"finish_reason":"stop"' not in str(output)
    assert '"error"' in output[1]
    assert PRIVATE not in str(output) + caplog.text


@pytest.mark.asyncio
async def test_chunk_after_completion_is_not_forwarded():
    output = await collect(
        [
            {
                "event_type": "agent_response_complete",
                "data": {"final_content": "已发布"},
            },
            {
                "event_type": "agent_content_chunk",
                "data": {"content_chunk": PRIVATE},
            },
        ]
    )
    assert PRIVATE not in str(output)
    assert '"error"' in output[1]


@pytest.mark.asyncio
async def test_consumer_close_immediately_closes_upstream():
    state = SimpleNamespace(closed=False)

    async def source():
        try:
            yield {
                "event_type": "agent_content_chunk",
                "data": {"content_chunk": "部分回答"},
            }
            await asyncio.Event().wait()
        finally:
            state.closed = True

    iterator = adapt(source())
    first = await anext(iterator)
    assert "部分回答" in first
    await iterator.aclose()
    assert state.closed


@pytest.mark.asyncio
async def test_task_cancellation_is_not_converted_to_a_success_or_error():
    state = SimpleNamespace(closed=False)
    entered = asyncio.Event()

    async def source():
        try:
            entered.set()
            await asyncio.Event().wait()
            yield {}
        finally:
            state.closed = True

    iterator = adapt(source())
    pending = asyncio.create_task(anext(iterator))
    await entered.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert state.closed


@pytest.mark.asyncio
async def test_existing_publication_pipeline_exposes_only_the_final_rewrite(
    monkeypatch,
):
    from datetime import datetime, timezone
    from uuid import uuid4
    from app.schemas.agent_execution import (
        AgentExecutionCheckReceipt,
        AgentExecutionReference,
    )
    from app.services import chat_service

    reference = AgentExecutionReference(
        project_id=uuid4(),
        agent_id=uuid4(),
        channel="internal",
        configuration_fingerprint="a" * 64,
        policy_revision="b" * 64,
    )

    async def provider(**kwargs):
        yield "agent_content_chunk", {"data": {"content_chunk": "内部草稿"}}
        yield "agent_response_complete", {
            "data": {
                "success": True,
                "final_content": "经过资料核验的回答",
                "execution_reference": reference.model_dump(mode="json"),
            }
        }
        yield "workflow_completed", {"data": {"success": True}}

    monkeypatch.setattr(
        chat_service.ai_client, "run_supervisor_agent_stream", provider
    )
    monkeypatch.setattr(
        chat_service.ai_client,
        "verify_agent_execution",
        AsyncMock(
            return_value=AgentExecutionCheckReceipt(
                reference=reference, checked_at=datetime.now(timezone.utc)
            )
        ),
    )
    monkeypatch.setattr(
        chat_service, "rewrite_assist_draft", AsyncMock(return_value="最终对外回答")
    )
    monkeypatch.setattr(
        chat_service, "recent_customer_messages", AsyncMock(return_value=[])
    )
    forward = AsyncMock()
    monkeypatch.setattr(chat_service, "forward_ai_event_to_wukongim", forward)
    output = [
        part
        async for part in adapt(
            chat_service.process_ai_stream_to_wukongim(
                project_id=str(reference.project_id),
                user_id="owned-visitor",
                message="商品问题",
                channel_id="owned-channel",
                channel_type=251,
                client_msg_no="owned-compatibility-reply",
                from_uid="owned-staff",
            )
        )
    ]
    assert "最终对外回答" in str(output)
    assert "内部草稿" not in str(output)
    assert output[-1] == "data: [DONE]\n\n"
    assert (
        len([part for part in output if '"finish_reason":"stop"' in part]) == 1
    )
    terminals = [
        call.kwargs
        for call in forward.await_args_list
        if call.kwargs["event_type"] == "workflow_completed"
    ]
    assert len(terminals) == 1
    assert terminals[0]["event_data"]["data"]["final_content"] == "最终对外回答"
    from app.services import ai_reply_control

    finished = await ai_reply_control.run_registry.get(
        str(reference.project_id), "owned-compatibility-reply"
    )
    assert finished is not None and finished.status == "completed"
