"""Selected styles preserve facts without being vetoed by default tone."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.humanization_service import rewrite_assist_draft
from app.services.reply_quality import ReplyQualityError, assess_reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply,question,facts,style",
    [
        ("您好，这款399元。感谢您的咨询。", "多少钱？", "售价399元。", "正式礼貌，使用您好和感谢。"),
        ("这款399元哦，蓝色有货啦。", "多少钱，蓝色有货吗？", "售价399元，蓝色有货。", "亲切口语化，可使用少量语气词。"),
        (
            "**退货步骤**\n1. 提交申请。\n2. 等待审核。",
            "怎么退货？",
            "提交退货申请后需要审核。",
            "复杂操作使用标题和分点。",
        ),
        (
            "需要查询哪笔订单？收货城市是哪里？",
            "帮我查订单预计什么时候到。",
            "查询预计送达时间需要订单号和收货城市。",
            "一次问清必要条件。",
        ),
    ],
)
async def test_selected_style_reaches_fact_audit_without_default_tone_repair(
    reply: str,
    question: str,
    facts: str,
    style: str,
) -> None:
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(
            side_effect=[
                {"content": reply},
                {"content": '{"valid":true,"issues":[]}'},
            ]
        )
    )
    result = await rewrite_assist_draft(
        client,
        project_id="p",
        agent_id=None,
        customer_message=question,
        factual_draft=facts,
        humanization_prompt=style,
    )
    assert result == reply
    assert client.run_supervisor_agent.await_count == 2
    prompt = client.run_supervisor_agent.call_args_list[0].kwargs[
        "system_message"
    ]
    assert style in prompt
    assert "默认表达风格" not in prompt
    assert "不反复用‘请您’" not in prompt
    audit_prompt = client.run_supervisor_agent.call_args_list[1].kwargs[
        "system_message"
    ]
    assert "问号数量" in audit_prompt


@pytest.mark.asyncio
async def test_default_tone_retries_without_blocking_checked_answer() -> None:
    reply = "温馨提示：提交退货申请后需要审核。"
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(
            side_effect=[
                {"content": reply},
                {"content": reply},
                {"content": '{"valid":true,"issues":[]}'},
            ]
        )
    )
    result = await rewrite_assist_draft(
        client,
        project_id="p",
        agent_id=None,
        customer_message="怎么退货？",
        factual_draft="提交退货申请后需要审核。",
    )
    assert result == reply
    assert (
        "默认表达风格"
        in client.run_supervisor_agent.call_args_list[0].kwargs[
            "system_message"
        ]
    )
    assert (
        "ceremonial_caveat"
        in client.run_supervisor_agent.call_args_list[1].kwargs[
            "system_message"
        ]
    )
    assert client.run_supervisor_agent.await_count == 3


@pytest.mark.asyncio
async def test_selected_style_cannot_change_a_price() -> None:
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(return_value={"content": "亲，这款299元哦。"})
    )
    with pytest.raises(ReplyQualityError, match="new_numbers"):
        await rewrite_assist_draft(
            client,
            project_id="p",
            agent_id=None,
            customer_message="多少钱？",
            factual_draft="售价399元。",
            humanization_prompt="使用亲和哦，语气亲切。",
        )
    assert client.run_supervisor_agent.await_count == 2


@pytest.mark.asyncio
async def test_selected_style_cannot_remove_an_approval_condition() -> None:
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(
            side_effect=[
                {"content": "提交申请就能退款啦。"},
                {
                    "content": (
                        '{"valid":false,'
                        '"issues":["approval_condition_removed"]}'
                    )
                },
                {"content": "提交申请就能退款啦。"},
                {
                    "content": (
                        '{"valid":false,'
                        '"issues":["approval_condition_removed"]}'
                    )
                },
            ]
        )
    )
    with pytest.raises(ReplyQualityError, match="approval_condition_removed"):
        await rewrite_assist_draft(
            client,
            project_id="p",
            agent_id=None,
            customer_message="怎么退款？",
            factual_draft="提交申请并审核通过后才能退款。",
            humanization_prompt="自然亲切，使用啦。",
        )


def test_list_markers_do_not_allow_a_changed_price() -> None:
    assert "new_numbers" in assess_reply(
        "1. 售价299元。\n2. 等待审核。",
        "售价399元，申请后需要审核。",
        "多少钱？",
        include_style=False,
    )
