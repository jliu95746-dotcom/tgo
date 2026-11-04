from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.schemas.humanization import ConversationTurn
from app.schemas.knowledge_evidence import KnowledgeEvidence
from app.services.current_knowledge import UNCONFIRMED_REPLY
from app.services.humanization_service import (
    recent_customer_messages,
    rewrite_assist_draft,
)
from app.services.wukongim_client import wukongim_client


@pytest.mark.asyncio
async def test_recent_native_employee_turns_survive_later_customer_and_ai_turns(monkeypatch):
    def message(seq, native=False):
        return SimpleNamespace(message_seq=seq, from_uid="employee-staff" if native else "customer-vtr",
            payload={"type": 1, "content": "在的亲" if native else f"后续咨询{seq}",
                "extra": {"source": "wecom_native", "native_role": "staff"} if native else {} })
    sync = AsyncMock(return_value=SimpleNamespace(messages=[message(1, True), *[message(i) for i in range(2, 25)]]))
    monkeypatch.setattr(wukongim_client, "sync_channel_messages", sync)
    turns = await recent_customer_messages("customer-vtr", 251, "customer-vtr")
    assert len(turns) == 8
    assert turns[0] == ConversationTurn(role="staff", content="在的亲")
    assert turns[-1].content == "后续咨询24"
    assert sync.await_args.kwargs["channel_id"] == "customer-vtr"


@pytest.mark.asyncio
async def test_verified_history_can_correct_false_claim_of_no_access_without_product_match():
    reply = "刚才人工客服回复了‘在的亲’。"
    client = SimpleNamespace(run_supervisor_agent=AsyncMock(side_effect=[
        {"content": reply}, {"content": '{"valid":true,"issues":[]}'},
    ]))
    result = await rewrite_assist_draft(client, project_id="project", agent_id=None,
        customer_message="刚才人工客服回复了什么？", factual_draft="我看不到人工记录。",
        recent_messages=[ConversationTurn(role="staff", content="在的亲")],
        knowledge_evidence=KnowledgeEvidence(status="no_match", retrieved_at=datetime.now(UTC), project_id="project", channel="wecom_kf"))
    assert result == reply
    assert client.run_supervisor_agent.await_count == 2
    for call in client.run_supervisor_agent.await_args_list:
        assert "员工之前说过什么" in call.kwargs["system_message"]
        assert "在的亲" in call.kwargs["message"]


@pytest.mark.asyncio
async def test_history_does_not_authorize_unverified_current_policy():
    client = SimpleNamespace(run_supervisor_agent=AsyncMock(side_effect=[
        {"content": "现在可以无条件退货。"},
        {"content": '{"valid":false,"issues":["unverified_current_policy"]}'},
        {"content": "现在可以无条件退货。"},
        {"content": '{"valid":false,"issues":["unverified_current_policy"]}'},
    ]))
    result = await rewrite_assist_draft(client, project_id="project", agent_id=None,
        customer_message="现在可以无条件退货吗？", factual_draft="无法确认。",
        recent_messages=[ConversationTurn(role="staff", content="之前可以无条件退货")],
        knowledge_evidence=KnowledgeEvidence(status="no_match", retrieved_at=datetime.now(UTC), project_id="project", channel="wecom_kf"))
    assert result == UNCONFIRMED_REPLY
