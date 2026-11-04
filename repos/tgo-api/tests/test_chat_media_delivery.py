"""Automatic media replies use the same checked publication boundary as text."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4
from datetime import datetime, timezone

import pytest
from app.schemas.agent_execution import AgentExecutionCheckReceipt, AgentExecutionReference

from app.schemas.chat_media import ChatMediaInput
from app.services import chat_service
from app.services import ai_interaction_run_service
from app.services.chat_media_analysis import PreparedChatMedia
from app.services.chat_media_service import MediaInputError


def test_business_query_signal_rejects_payload_and_mismatched_operation():
    query_id = uuid4()
    signal = {
        "tool_name": f"employee_business_member_{uuid4().hex}",
        "query_id": str(query_id), "operation": "membership_query",
        "outcome": "passed",
    }
    assert chat_service.read_business_query_signals([signal]) == [
        (query_id, "membership_query", "passed"),
    ]
    for changed in (
        {**signal, "order_no": "PRIVATE-ORDER"},
        {**signal, "operation": "order_query"},
        {**signal, "query_id": "invalid"},
    ):
        with pytest.raises(RuntimeError, match="business query"):
            chat_service.read_business_query_signals([changed])


@pytest.mark.asyncio
@pytest.mark.parametrize("conflicting_id", [False, True])
@pytest.mark.parametrize("failure_mode", ["none", "workflow_failed", "response_failed"])
async def test_customer_reply_records_private_ai_execution_without_exposing_it(
    monkeypatch, conflicting_id, failure_mode,
):
    project_id, visitor_id, interaction_id, execution_id, agent_id = (
        uuid4() for _ in range(5)
    )
    reference = AgentExecutionReference(
        project_id=project_id, agent_id=agent_id, channel="web",
        configuration_fingerprint="a" * 64, policy_revision="b" * 64,
    )
    bound = Mock()
    monkeypatch.setattr(ai_interaction_run_service, "record_ai_execution_attempt", bound)
    tool_events = Mock()
    monkeypatch.setattr(ai_interaction_run_service, "record_ai_tool_event", tool_events)
    business_queries = Mock()
    monkeypatch.setattr(ai_interaction_run_service, "record_ai_business_query", business_queries)
    query_id = uuid4()
    query_tool_name = f"employee_business_order_{uuid4().hex}"
    monkeypatch.setattr(chat_service, "forward_ai_event_to_wukongim", AsyncMock())
    monkeypatch.setattr(chat_service, "begin_reply_publication", AsyncMock())
    monkeypatch.setattr(chat_service, "ensure_customer_auto_reply", AsyncMock())
    monkeypatch.setattr(chat_service, "recent_customer_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(chat_service, "rewrite_assist_draft", AsyncMock(return_value="已核对"))
    monkeypatch.setattr(chat_service.ai_client, "verify_agent_execution", AsyncMock(
        return_value=AgentExecutionCheckReceipt(
            reference=reference, checked_at=datetime.now(timezone.utc),
        ),
    ))

    async def events(**_kwargs):
        yield "agent_execution_started", {
            "event_type": "agent_execution_started",
            "data": {"execution_id": str(execution_id)},
        }
        yield "agent_tool_call_started", {
            "event_type": "agent_tool_call_started",
            "data": {"execution_id": str(execution_id), "tool_name": "lookup_order",
                     "tool_call_id": "call-1", "status": "started",
                     "tool_input": {"password": "private-input"}},
        }
        yield "agent_tool_call_completed", {
            "event_type": "agent_tool_call_completed",
            "data": {"execution_id": str(execution_id), "tool_name": "lookup_order",
                     "tool_call_id": "call-1", "status": "completed",
                     "tool_output": "private-output",
                     "business_query_links": [] if failure_mode == "response_failed" else [{
                         "tool_name": query_tool_name, "query_id": str(query_id),
                         "operation": "order_query", "outcome": "passed",
                     }]},
        }
        if failure_mode == "workflow_failed":
            yield "workflow_failed", {"event_type": "workflow_failed", "data": {}}
            return
        yield "agent_response_complete", {
            "event_type": "agent_response_complete",
            "data": {"success": failure_mode != "response_failed",
                     "execution_id": str(uuid4() if conflicting_id else execution_id),
                     "final_content": "已核对",
                     "execution_reference": reference.model_dump(mode="json"),
                     "knowledge_evidence": {
                         "status": "skipped", "retrieved_at": datetime.now(timezone.utc).isoformat(),
                         "project_id": str(project_id), "channel": "web",
                         "tool_results": [{
                             "name": query_tool_name,
                             "content": "private-query-output", "success": True,
                             "query_id": str(query_id), "operation": "order_query",
                         }],
                     }},
        }
        yield "workflow_completed", {"event_type": "workflow_completed", "data": {}}

    monkeypatch.setattr(chat_service.ai_client, "run_supervisor_agent_stream", events)
    output = [item async for item in chat_service._process_ai_reply_to_wukongim(
        project_id=str(project_id), user_id=str(visitor_id), message="咨询",
        channel_id="owned-channel", channel_type=251, client_msg_no="reply-1",
        from_uid="agent", agent_id=str(agent_id), knowledge_channel="web",
        interaction_run_id=interaction_id,
    )]
    bound.assert_called_once_with(
        interaction_id, project_id, "reply-1", execution_id,
    )
    assert [call.args[4:] for call in tool_events.call_args_list] == [
        (1, "lookup_order", "call-1", "started"),
        (2, "lookup_order", "call-1", "completed"),
    ]
    assert output[-1]["event_type"] == (
        "workflow_failed" if conflicting_id or failure_mode != "none" else "workflow_completed"
    )
    assert str(execution_id) not in str(output)
    assert "private-input" not in str(output)
    assert "private-output" not in str(output)
    assert "private-query-output" not in str(output)
    assert str(query_id) not in str(output)
    if conflicting_id and failure_mode == "response_failed":
        business_queries.assert_not_called()
    else:
        business_queries.assert_called_once_with(
            interaction_id, project_id, "reply-1", execution_id,
            query_id, "order_query", "passed",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [True, False])
@pytest.mark.parametrize("failure", [False, True])
async def test_media_is_recognized_before_reply_and_failure_never_publishes(
    monkeypatch, stream, failure
):
    media = ChatMediaInput(
        project_id=uuid4(),
        platform_id=uuid4(),
        visitor_id=uuid4(),
        message_type=2,
        reference="/v1/chat/files/owned",
        source_message_id="source",
    )
    reference = AgentExecutionReference(
        project_id=media.project_id, agent_id=uuid4(), channel="internal",
        configuration_fingerprint="a" * 64, policy_revision="b" * 64,
    )
    verify = AsyncMock(return_value=AgentExecutionCheckReceipt(
        reference=reference, checked_at=datetime.now(timezone.utc),
    ))
    monkeypatch.setattr(chat_service.ai_client, "verify_agent_execution", verify)
    prepare = AsyncMock(
        side_effect=MediaInputError("请补充文字说明。") if failure else None,
        return_value=PreparedChatMedia("识别到一只红色包", "仅确认客户需求", True),
    )
    monkeypatch.setattr(chat_service, "prepare_chat_media", prepare)
    calls = []

    async def events(**kwargs):
        calls.append(kwargs)
        yield (
            "agent_response_complete",
            {"data": {"success": True, "final_content": "是想问这款包吗？",
                      "execution_reference": reference.model_dump(mode="json")}},
        )
        yield "workflow_completed", {"data": {"success": True}}

    monkeypatch.setattr(chat_service.ai_client, "run_supervisor_agent_stream", events)
    monkeypatch.setattr(
        chat_service, "rewrite_assist_draft", AsyncMock(return_value="是想问这款包吗？")
    )
    monkeypatch.setattr(
        chat_service, "recent_customer_messages", AsyncMock(return_value=[])
    )
    forward = AsyncMock()
    monkeypatch.setattr(chat_service, "forward_ai_event_to_wukongim", forward)
    kwargs = {
        "project_id": str(media.project_id),
        "message": media.reference,
        "channel_id": "channel",
        "channel_type": 251,
        "client_msg_no": "reply",
        "from_uid": "ai",
        "media_input": media,
    }
    if stream:
        output = [
            item
            async for item in chat_service.process_ai_stream_to_wukongim(
                user_id=str(media.visitor_id), **kwargs
            )
        ]
    else:
        output = await chat_service.handle_ai_response_non_stream(
            visitor_id=str(media.visitor_id), **kwargs
        )
    prepare.assert_awaited_once_with(media)
    published = [
        item
        for item in forward.await_args_list
        if item.kwargs["event_type"] == "workflow_completed"
    ]
    if failure:
        assert not calls and not published
        verify.assert_not_awaited()
        assert "请补充文字说明" in str(output)
    else:
        assert calls[0]["message"] == "识别到一只红色包"
        assert calls[0]["disable_tools"] is True
        assert "仅确认客户需求" in calls[0]["system_message"]
        assert len(published) == 1
        verify.assert_awaited_once_with(reference)


@pytest.mark.asyncio
async def test_background_media_context_is_carried_until_processing(monkeypatch):
    media = ChatMediaInput(
        project_id=uuid4(),
        platform_id=uuid4(),
        visitor_id=uuid4(),
        message_type=4,
        reference="/v1/chat/files/owned",
        source_message_id="source",
    )
    run = AsyncMock()
    monkeypatch.setattr(chat_service, "run_background_ai_interaction", run)
    task = chat_service.schedule_background_ai_interaction(
        project_id=str(media.project_id),
        user_id=str(media.visitor_id),
        message=media.reference,
        channel_id="channel",
        channel_type=251,
        client_msg_no="reply",
        from_uid="ai",
        media_input=media,
    )
    await task
    assert run.await_args.kwargs["media_input"] is media
