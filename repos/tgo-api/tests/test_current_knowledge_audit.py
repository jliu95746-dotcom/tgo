"""Old draft numbers and style examples are not current business evidence."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.schemas.knowledge_evidence import (
    BusinessToolEvidence,
    KnowledgeDocument,
    KnowledgeEvidence,
)
from app.services.reply_quality import assess_reply, audit_reply_facts
from app.services.humanization_service import rewrite_assist_draft
from app.services.current_knowledge import (
    UNCONFIRMED_REPLY,
    read_knowledge_evidence,
)


def evidence(text="晨光包299元，十五天退换。"):
    return KnowledgeEvidence(
        status="matched",
        retrieved_at=datetime.now(timezone.utc),
        project_id="project",
        channel="wecom_kf",
        documents=[
            KnowledgeDocument(
                collection_id="collection",
                document_id="document",
                content=text,
                relevance_score=0.8,
            )
        ],
    )


def test_old_price_in_draft_and_memory_is_not_numeric_authority():
    issues = assess_reply(
        "晨光包199元。",
        "晨光包199元。",
        "多少钱？",
        knowledge_evidence=evidence(),
        include_style=False,
    )
    assert "new_numbers" in issues
    assert "new_numbers" not in assess_reply(
        "晨光包299元。",
        "晨光包199元。",
        "多少钱？",
        knowledge_evidence=evidence(),
        include_style=False,
    )


@pytest.mark.asyncio
async def test_semantic_audit_receives_current_evidence_separately_from_draft():
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(
            return_value={
                "content": '{"valid":false,"issues":["outdated_policy"]}'
            }
        )
    )
    issues = await audit_reply_facts(
        client,
        project_id="project",
        agent_id=None,
        reply="七天退换。",
        factual_draft="七天退换。",
        customer_message="退换政策？",
        recent_messages=[{"role": "agent", "content": "七天退换。"}],
        knowledge_evidence=evidence(),
    )
    assert issues == ["outdated_policy"]
    import json

    payload = json.loads(
        client.run_supervisor_agent.call_args.kwargs["message"]
    )
    assert payload["本轮检索证据"]["documents"][0]["content"].endswith("十五天退换。")


@pytest.mark.asyncio
async def test_rewrite_corrects_stale_draft_and_ignores_old_style_example():
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(
            side_effect=[
                {"content": "晨光包299元，十五天退换。"},
                {"content": '{"valid":true,"issues":[]}'},
            ]
        )
    )
    reply = await rewrite_assist_draft(
        client,
        project_id="project",
        agent_id=None,
        customer_message="多少钱？退换呢？",
        factual_draft="晨光包199元，七天退换。",
        humanization_prompt="案例：199元，七天退换。",
        knowledge_evidence=evidence(),
    )
    assert "299" in reply and "十五天" in reply
    assert client.run_supervisor_agent.await_count == 2


@pytest.mark.asyncio
async def test_conflicting_policy_fails_closed_and_finishes_with_normal_reply():
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(
            side_effect=[
                {"content": "七天退换。"},
                {"content": '{"valid":false,"issues":["conflicting_policy"]}'},
                {"content": "十五天退换。"},
                {"content": '{"valid":false,"issues":["conflicting_policy"]}'},
            ]
        )
    )
    reply = await rewrite_assist_draft(
        client,
        project_id="project",
        agent_id=None,
        customer_message="退换政策？",
        factual_draft="七天退换。",
        knowledge_evidence=evidence("晨光包七天退换。晨光包十五天退换。"),
    )
    assert reply == UNCONFIRMED_REPLY


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["no_match", "unavailable", "over_budget"])
async def test_no_evidence_does_not_reuse_stale_handoff_receipt(state):
    current = evidence().model_copy(update={"status": state, "documents": []})
    client = SimpleNamespace(run_supervisor_agent=AsyncMock())
    reply = await rewrite_assist_draft(
        client,
        project_id="project",
        agent_id=None,
        customer_message="产品有哪些？",
        factual_draft="我们没有产品，已提交转人工申请。",
        knowledge_evidence=current,
    )
    assert reply == UNCONFIRMED_REPLY
    assert "已提交" not in reply and "没有产品" not in reply
    client.run_supervisor_agent.assert_not_awaited()


def test_invalid_tenant_or_channel_envelope_is_unavailable():
    for project, channel in [("foreign", "wecom_kf"), ("project", "web")]:
        parsed = read_knowledge_evidence(
            evidence().model_dump(), project_id=project, channel=channel
        )
        assert parsed.status == "unavailable" and not parsed.documents


def test_current_order_receipt_supports_historical_price_without_using_old_draft():
    current = evidence().model_copy(
        update={"status": "no_match", "documents": []}
    )
    current.tool_results.append(
        BusinessToolEvidence(
            name="get_order",
            content="该历史订单实际付款199元。",
            success=True,
        )
    )
    assert "new_numbers" not in assess_reply(
        "该订单实际付款199元。",
        "原价299元。",
        "这个订单多少钱？",
        knowledge_evidence=current,
        include_style=False,
    )
    current.tool_results[0].success = False
    assert "new_numbers" in assess_reply(
        "该订单实际付款199元。",
        "该订单199元。",
        "这个订单多少钱？",
        knowledge_evidence=current,
        include_style=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [True, False])
async def test_missing_evidence_completes_customer_reply_without_leaking_internal_data(
    monkeypatch, stream
):
    from app.services import chat_service

    async def events(**kwargs):
        assert kwargs["require_current_knowledge"] is True
        yield "agent_response_complete", {
            "data": {
                "success": True,
                "final_content": "旧价格199元。已转人工。",
            }
        }
        yield "workflow_completed", {"data": {"success": True}}

    monkeypatch.setattr(
        chat_service.ai_client, "run_supervisor_agent_stream", events
    )
    monkeypatch.setattr(
        chat_service, "recent_customer_messages", AsyncMock(return_value=[])
    )
    forward = AsyncMock()
    monkeypatch.setattr(chat_service, "forward_ai_event_to_wukongim", forward)
    kwargs = dict(
        project_id="project",
        message="产品多少钱？",
        channel_id="private-vtr",
        channel_type=251,
        client_msg_no="private-reply",
        from_uid="ai",
        knowledge_channel="wecom_kf",
    )
    if stream:
        output = [
            event
            async for event in chat_service.process_ai_stream_to_wukongim(
                user_id="v", **kwargs
            )
        ]
        assert output[-1]["event_type"] == "workflow_completed"
    else:
        output = await chat_service.handle_ai_response_non_stream(
            visitor_id="v", **kwargs
        )
        assert output["content"] == UNCONFIRMED_REPLY
    assert "knowledge_evidence" not in str(output)
    terminal = [
        call.kwargs
        for call in forward.await_args_list
        if call.kwargs["event_type"] == "workflow_completed"
    ]
    assert len(terminal) == 1
    assert (
        terminal[0]["event_data"]["data"]["final_content"] == UNCONFIRMED_REPLY
    )
