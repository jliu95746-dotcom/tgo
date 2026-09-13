"""Regression coverage for unrelated catalog details in customer answers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.humanization_service import (
    ASSIST_FACT_GATHERING_PROMPT,
    ASSIST_REWRITE_PROMPT,
    rewrite_assist_draft,
)
from app.services.reply_quality import FACT_AUDIT_PROMPT, ReplyQualityError


QUESTION = "有红色小羊皮女包吗？"
DIRECT_ANSWER = "有没有红色小羊皮款，现在还确认不了。"
UNRELATED_DETAILS = (
    "目前能确认的是星轨链条包 LV-C03，材质是超纤皮革，不是小羊皮，"
    "颜色有经典黑、月光银、香槟金。"
)
FACTS = DIRECT_ANSWER + UNRELATED_DETAILS


@pytest.mark.parametrize(
    "prompt",
    [ASSIST_FACT_GATHERING_PROMPT, ASSIST_REWRITE_PROMPT, FACT_AUDIT_PROMPT],
    ids=["facts", "rewrite", "audit"],
)
def test_every_stage_distinguishes_relevant_facts_from_catalog_padding(
    prompt: str,
) -> None:
    assert "不匹配的款式" in prompt
    assert "不算遗漏事实" in prompt
    assert "本轮明确" in prompt


@pytest.mark.asyncio
async def test_irrelevant_details_are_repaired_preserving_uncertainty() -> None:
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(
            side_effect=[
                {"content": FACTS},
                {"content": (
                    '{"valid":false,"issues":["irrelevant_product_details"]}'
                )},
                {"content": DIRECT_ANSWER},
                {"content": '{"valid":true,"issues":[]}'},
            ]
        )
    )
    reply = await rewrite_assist_draft(
        client,
        project_id="scope-test",
        agent_id=None,
        customer_message=QUESTION,
        factual_draft=FACTS,
        recent_messages=[
            {"role": "customer", "content": "请详细介绍现有几款女包。"},
            {"role": "staff", "content": "可以看看星轨链条包。"},
        ],
    )
    assert reply == DIRECT_ANSWER
    repair = client.run_supervisor_agent.call_args_list[2].kwargs
    assert "删去不匹配" in repair["system_message"]
    # Preserve source facts for the independent audit, not all in the answer.
    assert FACTS in repair["message"]
    assert repair["disable_tools"] is True
    assert repair["enable_memory"] is False


@pytest.mark.asyncio
async def test_repeated_irrelevant_details_never_release_raw_facts() -> None:
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(
            side_effect=[
                {"content": FACTS},
                {"content": (
                    '{"valid":false,"issues":["irrelevant_product_details"]}'
                )},
            ] * 2
        )
    )
    with pytest.raises(ReplyQualityError, match="irrelevant_product_details"):
        await rewrite_assist_draft(
            client, project_id="scope-test", agent_id=None,
            customer_message=QUESTION, factual_draft=FACTS,
        )


@pytest.mark.asyncio
async def test_explicit_comparison_keeps_requested_product_facts() -> None:
    question = "红色小羊皮有吗？如果没有合适的，也介绍星轨链条包的材质和颜色。"
    reply = DIRECT_ANSWER + "星轨链条包是超纤皮革，有经典黑、月光银、香槟金。"
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(
            side_effect=[
                {"content": reply},
                {"content": '{"valid":true,"issues":[]}'},
            ]
        )
    )
    assert await rewrite_assist_draft(
        client, project_id="scope-test", agent_id=None,
        customer_message=question, factual_draft=FACTS,
    ) == reply
