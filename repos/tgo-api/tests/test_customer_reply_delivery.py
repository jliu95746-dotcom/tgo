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
