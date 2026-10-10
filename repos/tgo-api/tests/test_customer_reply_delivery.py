from unittest.mock import AsyncMock
import pytest
from app.services import chat_service


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [True, False])
async def test_customer_only_gets_final_rewrite_in_stream_and_history(monkeypatch, stream):
    async def events(**kwargs):
        yield "agent_execution_started", {"data": {}}
        yield "agent_content_chunk", {"data": {"content_chunk": "我先查一下知识库。"}}
        yield "agent_tool_call_started", {"data": {}}
        yield "agent_content_chunk", {"data": {"content_chunk": "已确认这款没有绿色。"}}
        yield "agent_response_complete", {"data": {"success": True, "final_content": "已确认这款没有绿色。",
                                                       "content": "我先查一下知识库。", "message": "内部过程"}}
        yield "workflow_completed", {"data": {"success": True}}
    monkeypatch.setattr(chat_service.ai_client, "run_supervisor_agent_stream", events)
    monkeypatch.setattr(chat_service, "rewrite_assist_draft", AsyncMock(return_value="这款没有绿色。"))
    monkeypatch.setattr(chat_service, "recent_customer_messages", AsyncMock(return_value=[]))
    forward = AsyncMock()
    monkeypatch.setattr(chat_service, "forward_ai_event_to_wukongim", forward)
    kwargs = dict(project_id="p", message="绿色有吗？", channel_id="visitor-vtr", channel_type=251,
                  client_msg_no="m", from_uid="ai")
    if stream:
        output = [e async for e in chat_service.process_ai_stream_to_wukongim(user_id="v", **kwargs)]
        text = "".join(e["data"]["data"]["content_chunk"] for e in output if e["event_type"] == "agent_content_chunk")
    else:
        output = await chat_service.handle_ai_response_non_stream(visitor_id="v", **kwargs)
        text = output["content"]
    assert text == "这款没有绿色。"
    assert "知识库" not in str(output)
    terminal = [call.kwargs for call in forward.await_args_list if call.kwargs["event_type"] == "workflow_completed"]
    assert len(terminal) == 1
    assert terminal[0]["event_data"]["data"]["final_content"] == text


@pytest.mark.asyncio
@pytest.mark.parametrize("during_rewrite", [False, True])
async def test_handoff_during_generation_never_publishes_customer_reply(
    monkeypatch, during_rewrite,
):
    async def events(**kwargs):
        yield "agent_execution_started", {"data": {}}
        yield "workflow_completed", {"data": {
            "success": True, "final_content": "已经生成的回答",
        }}

    monkeypatch.setattr(chat_service.ai_client, "run_supervisor_agent_stream", events)
    rewrite = AsyncMock(return_value="已经改写的回答")
    monkeypatch.setattr(chat_service, "rewrite_assist_draft", rewrite)
    monkeypatch.setattr(chat_service, "recent_customer_messages", AsyncMock(return_value=[]))
    stopped = chat_service.ReplyStopped("已转人工，AI 自动回复已停止。")
    monkeypatch.setattr(chat_service, "ensure_customer_auto_reply", AsyncMock(
        side_effect=[None, stopped] if during_rewrite else stopped,
    ))
    forward = AsyncMock()
    monkeypatch.setattr(chat_service, "forward_ai_event_to_wukongim", forward)
    output = [event async for event in chat_service.process_ai_stream_to_wukongim(
        project_id="p", user_id="v", message="转人工", channel_id="visitor-vtr",
        channel_type=251, client_msg_no="handoff-reply", from_uid="staff",
    )]
    assert not any(event["event_type"] == "agent_content_chunk" for event in output)
    assert not any(call.kwargs["event_type"] == "workflow_completed"
                   for call in forward.await_args_list)
    assert output[-1]["event_type"] == "workflow_failed"
    if not during_rewrite:
        rewrite.assert_not_awaited()
