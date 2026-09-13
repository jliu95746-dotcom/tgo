"""Keep relevant business limits without generic product-answer disclaimers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.humanization_service import (
    ASSIST_FACT_GATHERING_PROMPT,
    ASSIST_REWRITE_PROMPT,
    rewrite_assist_draft,
)
from app.services.reply_quality import FACT_AUDIT_PROMPT, ReplyQualityError


@pytest.mark.parametrize(
    "prompt",
    [ASSIST_FACT_GATHERING_PROMPT, ASSIST_REWRITE_PROMPT, FACT_AUDIT_PROMPT],
    ids=["facts", "rewrite", "audit"],
)
def test_every_stage_limits_caveats_to_their_business_scope(prompt: str) -> None:
    assert "无关兜底" in prompt
    assert "不能把库存、价格或活动的动态提醒套到" in prompt
    assert "影响本轮答案的真实限制必须保留" in prompt


@pytest.mark.asyncio
async def test_color_caveat_is_repaired_without_published_skill() -> None:
    direct = "云朵法棍包没有绿色，有奶油白、可可棕和莓果红。"
    padded = direct + "颜色可能随批次或活动调整，以结算页实时显示为准。"
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(side_effect=[
            {"content": padded},
            {"content": '{"valid":false,"issues":["irrelevant_caveat"]}'},
            {"content": direct},
            {"content": '{"valid":true,"issues":[]}'},
        ])
    )
    assert await rewrite_assist_draft(
        client, project_id="caveat-test", agent_id=None,
        customer_message="云朵法棍包有绿色吗？", factual_draft=padded,
    ) == direct
    repair = client.run_supervisor_agent.call_args_list[2].kwargs
    assert "删除与本轮问题无关的兜底说明" in repair["system_message"]
    assert padded in repair["message"]  # Do not erase audit evidence.
    assert repair["disable_tools"] is True
    assert repair["enable_memory"] is False


@pytest.mark.parametrize("question,answer", [
    ("现在有货吗？", "库存实时变化，以结算页显示为准。"),
    ("偏远地区也包邮吗？", "偏远地区的运费以结算页显示为准。"),
    ("这个批次的绿色和图片一样吗？", "这个批次的绿色可能有色差，以实物为准。"),
    ("提交退款就会到账吗？", "提交后还需要审核，通过后才会退款。"),
])
@pytest.mark.asyncio
async def test_relevant_verified_conditions_are_not_blanket_deleted(
    question: str, answer: str,
) -> None:
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(side_effect=[
            {"content": answer},
            {"content": '{"valid":true,"issues":[]}'},
        ])
    )
    assert await rewrite_assist_draft(
        client, project_id="caveat-test", agent_id=None,
        customer_message=question, factual_draft=answer,
    ) == answer


@pytest.mark.asyncio
async def test_repeated_irrelevant_caveat_does_not_release_raw_draft() -> None:
    padded = "没有绿色。颜色可能随活动调整，以结算页为准。"
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(side_effect=[
            {"content": padded},
            {"content": '{"valid":false,"issues":["irrelevant_caveat"]}'},
        ] * 2)
    )
    with pytest.raises(ReplyQualityError, match="irrelevant_caveat"):
        await rewrite_assist_draft(
            client, project_id="caveat-test", agent_id=None,
            customer_message="有绿色吗？", factual_draft=padded,
        )
