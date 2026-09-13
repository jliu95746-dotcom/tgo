from app.schemas.humanization import TrainingExample, TrainingPublishRequest
from app.schemas.skill import HumanizationSkillCreateRequest, HumanizationTrainingSampleRequest
from app.services.skill_file_service import SkillFileService
import pytest


@pytest.mark.asyncio
async def test_review_publish_is_versioned_and_pending_does_not_affect_matching(tmp_path):
    service = SkillFileService(str(tmp_path))
    await service.create_humanization_skill("p", HumanizationSkillCreateRequest(
        name="natural-bags", display_name="卖包真人", description="商品咨询"))
    await service.add_humanization_training_sample("p", "natural-bags", HumanizationTrainingSampleRequest(
        customer_message="绿色有吗？", ai_draft="抱歉，目前没有绿色。请问您的预算？",
        final_reply="这款没有绿色。", source_message_id="msg-1"))
    preview = await service.review_humanization_training("p", "natural-bags")
    assert not (await service.match_humanization_context("p", "natural-bags", "绿色有吗？")).examples
    sample = preview.pending[0].model_copy(update={
        "change_kind": "expression", "scene_tags": ["颜色", "无匹配款"],
        "rules": ["简单颜色问题回答后直接结束，不追问预算。"], "selected": True})
    result = await service.publish_humanization_training("p", "natural-bags", TrainingPublishRequest(
        snapshot_id=preview.snapshot_id, samples=[sample]))
    assert result.published_version == 2
    match = await service.match_humanization_context("p", "natural-bags", "有没有绿色？")
    assert len(match.examples) == 1
    assert "预算" in match.rules[0]
    with pytest.raises(ValueError, match="changed"):
        await service.publish_humanization_training("p", "natural-bags", TrainingPublishRequest(
            snapshot_id=preview.snapshot_id, samples=[sample]))


@pytest.mark.asyncio
async def test_fact_correction_is_not_learned_as_style(tmp_path):
    service = SkillFileService(str(tmp_path))
    await service.create_humanization_skill("p", HumanizationSkillCreateRequest(
        name="natural-bags", display_name="卖包真人", description="商品咨询"))
    await service.add_humanization_training_sample("p", "natural-bags", HumanizationTrainingSampleRequest(
        customer_message="多少钱？", ai_draft="价格 300 元。", final_reply="价格 500 元。"))
    review = await service.review_humanization_training("p", "natural-bags")
    sample = review.pending[0].model_copy(update={"change_kind": "facts", "selected": True})
    await service.publish_humanization_training("p", "natural-bags", TrainingPublishRequest(
        snapshot_id=review.snapshot_id, samples=[sample]))
    assert not (await service.match_humanization_context("p", "natural-bags", "价格多少？")).examples


def test_matching_uses_intent_and_result_not_product_word_overlap():
    from app.services.humanization_library import HumanizationLibrary, StyleRelease

    samples = [
        TrainingExample(id="color", customer_message="法棍包有绿色吗？",
                        ai_draft="确认没有绿色。", final_reply="这款没有绿色。",
                        change_kind="expression", rules=["颜色咨询直接回答。"]),
        TrainingExample(id="price", customer_message="法棍包多少钱？",
                        ai_draft="售价399元。", final_reply="这款399元。",
                        change_kind="expression", rules=["价格咨询直接报价格。"]),
    ]
    release = StyleRelease(examples=samples, rules=[r for e in samples for r in e.rules])
    context = HumanizationLibrary.context("bags", "基础规则", release,
                                         "腋下包有没有蓝色？", "已确认没有蓝色。")
    assert [e.id for e in context.examples] == ["color"]
    assert context.rules == ["颜色咨询直接回答。"]
    unknown = HumanizationLibrary.context("bags", "基础规则", release,
                                         "法棍包有绿色吗？", "未找到绿色，不能确认是否有。")
    assert not unknown.examples
    assert not unknown.rules


@pytest.mark.asyncio
async def test_unreviewed_samples_cannot_be_silently_consumed(tmp_path):
    service = SkillFileService(str(tmp_path))
    await service.create_humanization_skill("p", HumanizationSkillCreateRequest(
        name="natural-bags", display_name="卖包真人", description="商品咨询"))
    await service.add_humanization_training_sample("p", "natural-bags", HumanizationTrainingSampleRequest(
        customer_message="多少钱？", ai_draft="售价300元。", final_reply="300元。"))
    review = await service.review_humanization_training("p", "natural-bags")
    with pytest.raises(ValueError, match="review"):
        await service.publish_humanization_training("p", "natural-bags", TrainingPublishRequest(
            snapshot_id=review.snapshot_id, samples=review.pending))
    assert len((await service.review_humanization_training("p", "natural-bags")).pending) == 1
