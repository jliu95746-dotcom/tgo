from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.humanization_service import rewrite_assist_draft
from app.services.reply_quality import ReplyQualityError, assess_reply


def test_quality_flags_process_language_and_fact_strengthening():
    assert "process_language" in assess_reply("我先查一下知识库，感谢您的耐心等待！", "没找到绿色款。", "绿色有吗？")
    assert "certainty_changed" in assess_reply("没有绿色款。", "未找到绿色款，库存尚未确认。", "绿色有吗？")
    assert not assess_reply("这款没有绿色。", "已确认这款没有绿色。", "绿色有吗？")


@pytest.mark.parametrize(
    "reply",
    [
        "红色小羊皮的包有没有，现在还确认不了。",
        "现在还不清楚有没有红色小羊皮的包。",
        "有没有红色小羊皮的，现在还不能确认。",
    ],
)
def test_natural_uncertainty_is_not_mistaken_for_definite_absence(reply):
    assert not assess_reply(reply, "未找到红色小羊皮的款式，是否有还不能确认。", "红色小羊皮有吗？")


@pytest.mark.parametrize(
    "question,reply",
    [
        ("支持 API 对接吗？", "支持 API 对接。"),
        ("模型玩具有吗？", "有，这款是模型玩具。"),
        ("知识库怎么编辑？", "打开知识库，选择文章后点击编辑。"),
        ("这个接口做什么？", "这个接口用于订单查询场景。"),
        ("这款多少钱？", "这款 1,299 元。"),
        ("接口地址是什么？", "接口地址是 https://example.com/api/orders?limit=20。"),
    ],
)
def test_legitimate_topic_and_number_format_are_not_blocked(question, reply):
    facts = "这款售价 1299 元。" if "1,299" in reply else reply
    assert not assess_reply(reply, facts, question)


@pytest.mark.parametrize(
    "reply",
    [
        "好的，我先查一下。",
        "我查了一下，暂时还不能确认。",
        "我先调用 API 查询一下。",
        "根据知识库，这款是牛皮的。",
        "知识库中暂未找到这款商品。",
        "部分款式的材质信息暂未明确列出。",
    ],
)
def test_process_narration_stays_blocked_even_for_technical_questions(reply):
    assert "process_language" in assess_reply(reply, reply, "API 怎么接？")


@pytest.mark.parametrize("reply", ["这款 299 元。", "这款 12,990 元。"])
def test_formatted_prices_cannot_hide_a_changed_amount(reply):
    assert "new_numbers" in assess_reply(reply, "这款 1299 元。", "这款多少钱？")


def test_unrelated_followup_is_checked_against_recent_customer_context():
    assert "unnecessary_question" in assess_reply("预算大概多少？", "已确认没有绿色。", "绿色有吗？")
    assert not assess_reply(
        "预算大概多少？",
        "推荐前需要知道客户预算。",
        "那你推荐吧。",
        recent_messages=[{"role": "customer", "content": "推荐一款通勤包。"}],
    )


def test_product_scenes_do_not_turn_a_product_question_into_a_budget_question():
    reply = "通勤包适合上班场景，小方包适合休闲场合。您想先了解哪款？"
    assert "unnecessary_question" not in assess_reply(
        reply, "通勤包适合上班场景，小方包适合休闲场合。",
        "你能介绍一下你们的产品吗？", include_style=False,
    )
    assert "unnecessary_question" in assess_reply(
        reply + "您的预算是多少？", reply, "介绍一下产品。",
        include_style=False,
    )


@pytest.mark.asyncio
async def test_unnecessary_budget_question_receives_actionable_repair():
    client = SimpleNamespace(run_supervisor_agent=AsyncMock(side_effect=[
        {"content": "我们有通勤女包。您的预算是多少？"},
        {"content": "我们有通勤女包。"},
        {"content": '{"valid":true,"issues":[]}'},
    ]))
    reply = await rewrite_assist_draft(
        client, project_id="p", agent_id=None,
        customer_message="介绍一下产品。", factual_draft="我们有通勤女包。",
    )
    assert reply == "我们有通勤女包。"
    repair = client.run_supervisor_agent.call_args_list[1].kwargs["system_message"]
    assert "删除与本轮问题无关的预算、使用场合或场景追问" in repair


@pytest.mark.asyncio
async def test_repeated_optional_questions_do_not_block_checked_introduction():
    draft = "我们有通勤女包，售价399元。"
    reply = draft + "想帮你挑得更准，平时主要什么场合用？预算大概什么范围？"
    client = SimpleNamespace(run_supervisor_agent=AsyncMock(side_effect=[
        {"content": reply}, {"content": reply},
        {"content": '{"valid":true,"issues":[]}'},
    ]))
    result = await rewrite_assist_draft(
        client, project_id="p", agent_id=None,
        customer_message="介绍一下产品。", factual_draft=draft,
        humanization_prompt="自然介绍，可引导选购。",
    )
    assert result == draft
    assert client.run_supervisor_agent.await_count == 3
    repair = client.run_supervisor_agent.call_args_list[1].kwargs["system_message"]
    assert "自然介绍，可引导选购。" not in repair
    assert "默认表达风格" in repair
    assert '"待审核回复": "我们有通勤女包，售价399元。"' in (
        client.run_supervisor_agent.call_args_list[-1].kwargs["message"]
    )


@pytest.mark.asyncio
async def test_optional_question_cleanup_cannot_bypass_fact_audit():
    reply = "我们有通勤女包，提交退货申请就能退款。预算多少？"
    client = SimpleNamespace(run_supervisor_agent=AsyncMock(side_effect=[
        {"content": reply}, {"content": reply},
        {"content": '{"valid":false,"issues":["approval_condition_removed"]}'},
    ]))
    with pytest.raises(ReplyQualityError, match="approval_condition_removed"):
        await rewrite_assist_draft(
            client, project_id="p", agent_id=None,
            customer_message="介绍一下产品和售后。",
            factual_draft="我们有通勤女包，退货申请需要审核。",
        )


@pytest.mark.parametrize(
    "reply",
    [
        "红色小羊皮款还确认不了有没有，建议您先参考其他材质或颜色的款式。",
        "这款没有绿色，可以看看其他颜色。",
        "这款没有绿色，要不考虑别的款式？",
        "这款没有绿色，也可以看看牛皮款。",
    ],
)
def test_unrequested_alternative_suggestions_are_blocked(reply):
    assert "unsolicited_alternative" in assess_reply(
        reply, reply, "绿色的法棍包有吗？"
    )


@pytest.mark.parametrize("question", ["推荐别的款吧。", "有没有其他颜色的款式？"])
def test_requested_alternatives_are_allowed(question):
    reply = "可以看看其他颜色，这款有蓝色。"
    assert not assess_reply(reply, reply, question)


def test_alternative_request_uses_customer_context_but_respects_latest_refusal():
    reply = "可以看看其他颜色，这款有蓝色。"
    recent = [{"role": "customer", "content": "没有绿色就推荐别的颜色。"}]
    assert not assess_reply(reply, reply, "那还有呢？", recent_messages=recent)
    assert "unsolicited_alternative" in assess_reply(
        reply, reply, "不要推荐其他款，只要绿色。", recent_messages=recent
    )
    assert "unsolicited_alternative" in assess_reply(
        reply, reply, "绿色有吗？",
        recent_messages=[{"role": "staff", "content": "我给您推荐其他颜色。"}],
    )


def test_alternative_guard_does_not_block_troubleshooting_advice():
    reply = "可以换其他浏览器再试一下。"
    assert not assess_reply(reply, reply, "登录页面打不开怎么办？")


@pytest.mark.parametrize(
    "reply",
    [
        "目前红色小羊皮的女包暂时没查到，具体有没有还确认不了。",
        "红色款没有找到，到底有没有现在还不确定。",
        "红色款未找到，是否有暂时无法确认。",
    ],
)
def test_lookup_then_same_uncertainty_is_rejected(reply):
    assert "redundant_lookup_uncertainty" in assess_reply(
        reply, "有没有红色小羊皮女包尚未确认。", "红色小羊皮有吗？"
    )


def test_distinct_lookup_and_material_facts_are_not_collapsed():
    reply = "红色款没找到，蓝色款的材质还确认不了。"
    assert not assess_reply(reply, reply, "红色款有吗，蓝色款是什么材质？")


@pytest.mark.asyncio
async def test_unsolicited_alternative_is_rewritten_before_fact_audit():
    client = SimpleNamespace(run_supervisor_agent=AsyncMock(side_effect=[
        {"content": "这款没有绿色，可以看看其他颜色。"},
        {"content": "这款没有绿色。"},
        {"content": '{"valid":true,"issues":[]}'},
    ]))
    reply = await rewrite_assist_draft(
        client, project_id="p", agent_id=None, customer_message="绿色有吗？",
        factual_draft="这款没有绿色。",
    )
    assert reply == "这款没有绿色。"
    assert "unsolicited_alternative" in client.run_supervisor_agent.call_args_list[1].kwargs["system_message"]


@pytest.mark.asyncio
async def test_technical_answer_reaches_independent_fact_audit():
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(
            side_effect=[
                {"content": "支持 API 对接。"},
                {"content": '{"valid":true,"issues":[]}'},
            ]
        )
    )
    assert (
        await rewrite_assist_draft(
            client,
            project_id="p",
            agent_id=None,
            customer_message="支持 API 对接吗？",
            factual_draft="已确认支持 API 对接。",
        )
        == "支持 API 对接。"
    )
    assert client.run_supervisor_agent.await_count == 2


@pytest.mark.asyncio
async def test_template_greeting_is_removed_before_fact_audit() -> None:
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(
            side_effect=[
                {"content": "您好，请问需要咨询什么具体问题？"},
                {"content": "您好，请问需要咨询什么具体问题？"},
                {"content": '{"valid":true,"issues":[]}'},
            ]
        )
    )

    reply = await rewrite_assist_draft(
        client,
        project_id="test-project",
        agent_id=None,
        customer_message="AI客服联调测试1006",
        factual_draft="请问需要咨询什么具体问题？",
    )

    assert reply == "请问需要咨询什么具体问题？"
    assert client.run_supervisor_agent.await_count == 3
    assert reply in client.run_supervisor_agent.call_args_list[2].kwargs["message"]


@pytest.mark.asyncio
async def test_rewrite_never_releases_unchecked_factual_draft():
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(
            return_value={"content": "我查了一下知识库，部分款式的材质信息暂未明确列出。"}
        )
    )
    with pytest.raises(ReplyQualityError):
        await rewrite_assist_draft(
            client,
            project_id="p",
            agent_id=None,
            customer_message="红色小羊皮有吗？",
            factual_draft="材质尚未确认。",
        )


@pytest.mark.asyncio
async def test_rewrite_receives_recent_customer_context_and_isolated_profile():
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(
            side_effect=[
                {"content": "这款没有绿色。"},
                {"content": '{"valid":true,"issues":[]}'},
            ]
        )
    )
    await rewrite_assist_draft(
        client,
        project_id="p",
        agent_id=None,
        customer_message="绿色有吗？",
        factual_draft="已确认没有绿色。",
        recent_messages=[{"role": "customer", "content": "预算五百，上班背。"}],
    )
    args = client.run_supervisor_agent.call_args_list[0].kwargs
    assert args["response_purpose"] == "expression"
    assert "预算五百，上班背" in args["message"]
    assert args["enable_memory"] is False


@pytest.mark.asyncio
async def test_semantic_check_catches_invented_material_without_new_numbers():
    client = SimpleNamespace(
        run_supervisor_agent=AsyncMock(
            side_effect=[
                {"content": "这款是小羊皮的。"},
                {"content": '{"valid":false,"issues":["invented_material"]}'},
                {"content": "这款是小羊皮的。"},
                {"content": '{"valid":false,"issues":["invented_material"]}'},
            ]
        )
    )
    with pytest.raises(ReplyQualityError, match="invented_material"):
        await rewrite_assist_draft(
            client,
            project_id="p",
            agent_id=None,
            customer_message="这款什么材质？",
            factual_draft="这款是牛皮。",
        )


@pytest.mark.asyncio
async def test_oversized_facts_are_not_silently_truncated():
    client = SimpleNamespace(run_supervisor_agent=AsyncMock())
    with pytest.raises(ReplyQualityError, match="too long"):
        await rewrite_assist_draft(
            client,
            project_id="p",
            agent_id=None,
            customer_message="有哪些限制？",
            factual_draft="条件" * 6000,
        )
    client.run_supervisor_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_recent_context_includes_finished_ai_reply_but_not_process_text(
    monkeypatch,
):
    from app.services.humanization_service import recent_customer_messages

    messages = [
        SimpleNamespace(
            message_seq=1, from_uid="v-vtr", payload={"type": 1, "content": "多少钱？"}
        ),
        SimpleNamespace(
            message_seq=2,
            from_uid="agent",
            payload={"type": 100, "content": "我先查一下"},
            end=1,
            error=None,
            event_meta={
                "completed": True,
                "events": [
                    {
                        "event_key": "main",
                        "snapshot": {"kind": "text", "text": "这款399元。"},
                    }
                ],
            },
        ),
        SimpleNamespace(
            message_seq=3,
            from_uid="agent",
            payload={"type": 100, "content": "查询中"},
            end=0,
            error=None,
            event_meta={},
        ),
    ]
    sync = AsyncMock(return_value=SimpleNamespace(messages=messages))
    monkeypatch.setattr(
        "app.services.wukongim_client.wukongim_client.sync_channel_messages", sync
    )
    turns = await recent_customer_messages("v-vtr", 251, "staff")
    assert [t.content for t in turns] == ["多少钱？", "这款399元。"]
    assert turns[1].role == "assistant"
    assert sync.call_args.kwargs["include_event_meta"] == 1
