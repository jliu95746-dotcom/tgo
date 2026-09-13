from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.humanization_service import rewrite_assist_draft
from app.services.reply_quality import assess_reply


RETURN_FACTS = "在订单详情页点击申请售后，选择退货退款，填写原因并提交。" "提交申请不代表审核通过。"


@pytest.mark.parametrize(
    "reply",
    [
        "我能查到的是云朵法棍包 LV-B02。",
        "我目前找到的是云朵法棍包 LV-B02。",
        "绿色还确认不了，我再帮你核实。",
        "我会帮您确认一下。",
    ],
)
def test_lookup_narration_and_future_checks_are_not_customer_answers(
    reply: str,
) -> None:
    assert "process_language" in assess_reply(reply, reply, "绿色的法棍包有吗？")


@pytest.mark.parametrize(
    "reply",
    [
        "你说的是云朵法棍包 LV-B02 吗？绿色现在还确认不了。",
        "可以在订单详情页查询物流。",
        "退款已核实，款项已原路退回。",
    ],
)
def test_query_instructions_and_completed_business_results_remain_valid(
    reply: str,
) -> None:
    assert not assess_reply(reply, reply, "请说明当前情况。")


@pytest.mark.parametrize(
    "reply,issue",
    [
        ("您好，在订单详情页申请售后。", "template_opening"),
        ("亲亲，这款售价399元。", "template_opening"),
        (
            "提交申请就可以啦。不过要提醒您，申请不代表审核通过哦。",
            "ceremonial_caveat",
        ),
        ("温馨提示：提交申请不代表审核通过。", "ceremonial_caveat"),
        ("提交申请就可以啦，审核通过才会退款哦。", "forced_friendly_particles"),
    ],
)
def test_business_answers_reject_performative_service_tone(
    reply: str, issue: str
) -> None:
    assert issue in assess_reply(reply, reply, "怎么退货？")


@pytest.mark.parametrize(
    "question,reply",
    [
        ("你好", "你好，有什么需要帮忙的？"),
        ("您好！", "您好！"),
        ("怎么退货？", "在订单详情页申请售后。提交申请后还需要审核。"),
        ("怎么退货？", "请保留包装，提交申请后还需要审核。"),
        ("现在可以申请吗？", "可以呀，在订单详情页申请。"),
        ("哪里查看规则？", "点击“温馨提示”查看规则。"),
        ("怎么退货？", "审核通过后才会退款，提交申请不代表审核通过。"),
    ],
)
def test_direct_answers_keep_greetings_context_and_necessary_conditions(
    question: str, reply: str
) -> None:
    assert not assess_reply(reply, reply, question)


@pytest.mark.parametrize("question", ["你好", "您好！", "Hi", "哈喽～"])
@pytest.mark.parametrize(
    "reply",
    [
        "你好，请问有什么可以帮您？无论是了解女包的款式、材质、颜色、价格，" "还是查询订单、物流、退换货或保养问题，都可以告诉我。",
        "你好，可以说下使用场景和预算，我帮你挑选。",
        "你好，选包、物流、售后都可以问我。",
    ],
)
def test_greeting_does_not_resume_old_shopping_intent(question, reply):
    issues = assess_reply(
        reply,
        reply,
        question,
        recent_messages=[{"role": "customer", "content": "预算500，推荐一款包"}],
    )
    assert "greeting_overreach" in issues


@pytest.mark.parametrize(
    "question,reply",
    [
        ("你好，预算500元有什么推荐？", "可以看看这款399元的包。"),
        ("你好，订单怎么查？", "在订单页面查看物流。"),
        ("介绍一下能帮我做什么", "可以咨询商品、订单、物流和售后。"),
        ("你好", "你好，有什么需要帮忙的？"),
    ],
)
def test_greeting_rule_does_not_remove_explicit_business_requests(question, reply):
    assert "greeting_overreach" not in assess_reply(reply, reply, question)


@pytest.mark.asyncio
async def test_long_greeting_is_repaired_and_audited_with_old_context():
    draft = "你好，可以说下使用场景和预算，我帮你挑选。"
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(
            side_effect=[
                {"content": draft},
                {"content": "你好，有什么需要帮忙的？"},
                {"content": '{"valid":true,"issues":[]}'},
            ]
        )
    )
    reply = await rewrite_assist_draft(
        client,
        project_id="test-project",
        agent_id=None,
        customer_message="你好",
        factual_draft=draft,
        recent_messages=[{"role": "customer", "content": "推荐一款500元的包"}],
    )
    assert reply == "你好，有什么需要帮忙的？"
    repair = client.run_supervisor_agent.call_args_list[1].kwargs
    assert "greeting_overreach" in repair["system_message"]
    assert "业务清单" in repair["system_message"]
    assert repair["disable_tools"] is True
    assert repair["enable_memory"] is False
    assert client.run_supervisor_agent.await_count == 3


@pytest.mark.asyncio
async def test_return_answer_is_repaired_without_losing_approval_condition() -> None:
    natural_reply = "在订单详情页点“申请售后”，选“退货退款”，填写原因后提交。" "提交申请后还需要审核。"
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(
            side_effect=[
                {
                    "content": "您好，退货的话，请您在订单详情页点击申请售后，"
                    "选择退货退款，填写原因后提交就可以啦。"
                    "不过要提醒您，提交申请不代表一定会审核通过哦。"
                },
                {"content": natural_reply},
                {"content": '{"valid":true,"issues":[]}'},
            ]
        )
    )

    reply = await rewrite_assist_draft(
        client,
        project_id="test-project",
        agent_id=None,
        customer_message="怎么退货？",
        factual_draft=RETURN_FACTS,
    )

    assert reply == natural_reply
    assert client.run_supervisor_agent.await_count == 3
    repair = client.run_supervisor_agent.call_args_list[1].kwargs["system_message"]
    assert "template_opening" in repair
    assert "保留" in repair and "条件" in repair
    audit = client.run_supervisor_agent.call_args_list[2].kwargs["message"]
    assert natural_reply in audit
    assert RETURN_FACTS in audit
