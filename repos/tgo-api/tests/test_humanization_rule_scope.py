import json
from unittest.mock import AsyncMock, Mock

import pytest

from app.schemas.humanization import TrainingExample, TrainingReview
from app.services import humanization_review_service as review_service


@pytest.mark.asyncio
@pytest.mark.parametrize("fenced", [False, True])
async def test_single_clarification_does_not_become_a_mandatory_followup_rule(
    monkeypatch: pytest.MonkeyPatch,
    fenced: bool,
) -> None:
    sample = TrainingExample(
        id="isolated-sample",
        customer_message="绿色的法棍包有吗？",
        ai_draft="我能查到的是 LV-B02，但绿色还确认不了。您问的是这款吗？",
        final_reply="绿色现在还确认不了。你说的是 LV-B02 吗？",
    )
    review = TrainingReview(
        name="isolated-skill",
        published_version=1,
        snapshot_id="s" * 32,
        pending=[sample],
        published=[],
        rules=[],
    )
    safe_rules = [
        "不确定时直接说明还不能确认，删除查找过程。",
        "只有客户未说明具体款式且本轮事实已有候选型号时，才追问一次确认款式。",
        "不要重复追问客户已经提供的条件。",
    ]
    generated_rules = [
        "在无法确认商品信息时，先给结论，再提出一个澄清性问题。",
        "客户问颜色而库存不确定时，应立即反问客户是否指代具体型号。",
        *safe_rules,
    ]
    run = AsyncMock(
        return_value={
            "content": json.dumps(
                {
                    "samples": [
                        {
                            "id": sample.id,
                            "change_kind": "expression",
                            "scene_tags": ["颜色确认"],
                            "rules": generated_rules,
                            "warnings": [],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        }
    )
    if fenced:
        run.return_value["content"] = (
            "```json\n" + run.return_value["content"] + "\n```"
        )
    monkeypatch.setattr(
        review_service.ai_client,
        "review_humanization_training",
        AsyncMock(return_value=review),
    )
    monkeypatch.setattr(review_service.ai_client, "run_supervisor_agent", run)

    result = await review_service.analyze_training("isolated-project", review.name)

    assert result.pending[0].rules == safe_rules
    assert result.pending[0].selected is True
    assert result.pending[0].final_reply == sample.final_reply
    assert any("追问" in warning for warning in result.pending[0].warnings)
    assert "缺少" in run.call_args.kwargs["system_message"]
    assert result.published == []
    assert result.published_version == 1


@pytest.mark.asyncio
async def test_minor_expression_edit_can_be_kept_without_inventing_a_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = TrainingExample(
        id="word-order",
        customer_message="绿色的法棍包有吗？",
        ai_draft="绿色法棍包现在确认不了有没有。",
        final_reply="有没有绿色的法棍包，现在还不确定。",
    )
    review = TrainingReview(
        name="isolated-skill",
        published_version=2,
        snapshot_id="s" * 32,
        pending=[sample],
        published=[],
        rules=[],
    )
    run = AsyncMock(
        return_value={
            "content": json.dumps(
                {
                    "samples": [
                        {
                            "id": sample.id,
                            "change_kind": "expression",
                            "scene_tags": ["颜色咨询", "不确定回复"],
                            "rules": [],
                            "warnings": [],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        }
    )
    monkeypatch.setattr(
        review_service.ai_client,
        "review_humanization_training",
        AsyncMock(return_value=review),
    )
    monkeypatch.setattr(review_service.ai_client, "run_supervisor_agent", run)

    result = await review_service.analyze_training("p", review.name)

    prompt = run.call_args.kwargs["system_message"]
    assert "没有承诺不等于未执行承诺" in prompt
    assert "仅调整语序" in prompt
    assert "rules 留空" in prompt
    assert result.pending[0].selected is True
    assert result.pending[0].rules == []
    assert result.pending[0].final_reply == sample.final_reply
    assert result.published_version == 2


@pytest.mark.asyncio
async def test_actual_unverified_promise_remains_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = TrainingExample(
        id="promise",
        customer_message="绿色的法棍包有吗？",
        ai_draft="有没有绿色法棍包，现在还不确定。",
        final_reply="有没有绿色法棍包，现在还不确定。我十分钟后回复你。",
    )
    review = TrainingReview(
        name="isolated-skill",
        published_version=2,
        snapshot_id="s" * 32,
        pending=[sample],
        published=[],
        rules=[],
    )
    run = AsyncMock(
        return_value={
            "content": json.dumps(
                {
                    "samples": [
                        {
                            "id": sample.id,
                            "change_kind": "review",
                            "rules": [],
                            "warnings": ["人工回复新增‘我十分钟后回复你’的未确认承诺。"],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        }
    )
    monkeypatch.setattr(
        review_service.ai_client,
        "review_humanization_training",
        AsyncMock(return_value=review),
    )
    monkeypatch.setattr(review_service.ai_client, "run_supervisor_agent", run)

    result = await review_service.analyze_training("p", review.name)

    assert result.pending[0].selected is False
    assert result.pending[0].change_kind == "review"
    assert result.pending[0].warnings
    assert result.published_version == 2


@pytest.mark.asyncio
async def test_analysis_failure_logs_error_type_without_customer_or_model_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_text = "private-test-message-must-not-be-logged"
    review = TrainingReview(
        name="isolated-skill",
        published_version=1,
        snapshot_id="s" * 32,
        pending=[
            TrainingExample(
                id="s1", customer_message=private_text, ai_draft="原稿", final_reply="修改"
            )
        ],
        published=[],
        rules=[],
    )
    warning = Mock()
    monkeypatch.setattr(
        review_service.ai_client,
        "review_humanization_training",
        AsyncMock(return_value=review),
    )
    monkeypatch.setattr(
        review_service.ai_client,
        "run_supervisor_agent",
        AsyncMock(return_value={"content": private_text}),
    )
    monkeypatch.setattr(review_service.logger, "warning", warning)
    result = await review_service.analyze_training("p", review.name)
    assert result.pending[0].selected is False
    assert result.pending[0].final_reply == "修改"
    assert "ValidationError" in str(warning.call_args)
    assert private_text not in str(warning.call_args)


@pytest.mark.asyncio
async def test_non_text_rules_receive_one_format_repair_before_being_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = TrainingExample(
        id="s1", customer_message="有绿色吗？", ai_draft="绿色还确认不了。", final_reply="绿色还不能确认。"
    )
    review = TrainingReview(
        name="isolated-skill",
        published_version=1,
        snapshot_id="s" * 32,
        pending=[sample],
        published=[],
        rules=[],
    )
    wrong = {
        "id": "s1",
        "change_kind": "expression",
        "rules": [{"condition": "颜色不确定", "instruction": "直接表达不确定性"}],
    }
    repaired = {**wrong, "rules": ["颜色无法确认时直接说明不确定性，不描述查询过程。"]}
    run = AsyncMock(
        side_effect=[
            {"content": json.dumps({"samples": [wrong]}, ensure_ascii=False)},
            {"content": json.dumps({"samples": [repaired]}, ensure_ascii=False)},
        ]
    )
    monkeypatch.setattr(
        review_service.ai_client,
        "review_humanization_training",
        AsyncMock(return_value=review),
    )
    monkeypatch.setattr(review_service.ai_client, "run_supervisor_agent", run)

    result = await review_service.analyze_training("p", review.name)

    assert run.await_count == 2
    assert "字符串" in run.call_args.kwargs["system_message"]
    assert result.pending[0].selected is True
    assert result.pending[0].rules == repaired["rules"]
    assert result.published_version == 1
