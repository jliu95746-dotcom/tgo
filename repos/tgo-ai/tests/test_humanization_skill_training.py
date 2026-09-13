from pathlib import Path

import pytest

from app.schemas.humanization import TrainingPublishRequest
from app.schemas.skill import (
    HumanizationSkillCreateRequest,
    HumanizationTrainingSampleRequest,
)
from app.services.skill_file_service import SkillFileService


@pytest.mark.asyncio
async def test_delivery_correction_is_idempotent_and_preserves_customer_source(
    tmp_path: Path,
) -> None:
    service = SkillFileService(str(tmp_path))
    skill = await service.create_humanization_skill(
        "owned-project",
        HumanizationSkillCreateRequest(
            name="style-a",
            display_name="测试口语",
            description="独立测试",
        ),
    )
    sample = HumanizationTrainingSampleRequest(
        delivery_id="delivery-one",
        source_message_id="customer-one",
        customer_message="有绿色吗？",
        ai_draft="抱歉，目前没有绿色。",
        final_reply="没有绿色。",
    )
    first = await service.add_humanization_training_sample(
        "owned-project", skill.name, sample
    )
    repeated = await service.add_humanization_training_sample(
        "owned-project", skill.name, sample
    )
    assert first.pending_training_count == repeated.pending_training_count == 1
    with pytest.raises(ValueError, match="cannot be changed"):
        await service.add_humanization_training_sample(
            "owned-project",
            skill.name,
            sample.model_copy(update={"final_reply": "有绿色。"}),
        )
    another = sample.model_copy(
        update={"delivery_id": "delivery-two", "final_reply": "这款没有绿色。"}
    )
    second = await service.add_humanization_training_sample(
        "owned-project", skill.name, another
    )
    assert second.pending_training_count == 2
    review = await service.review_humanization_training("owned-project", skill.name)
    assert {item.source_message_id for item in review.pending} == {"customer-one"}
    assert len({item.id for item in review.pending}) == 2
    assert review.published_version == 1
    assert not review.published

    approved = review.pending[0].model_copy(
        update={"selected": True, "change_kind": "expression"}
    )
    await service.publish_humanization_training(
        "owned-project",
        skill.name,
        TrainingPublishRequest(snapshot_id=review.snapshot_id, samples=[approved]),
    )
    resumed = SkillFileService(str(tmp_path))
    after_publish = await resumed.add_humanization_training_sample(
        "owned-project", skill.name, sample
    )
    assert (
        after_publish.published_version == 2
        and after_publish.pending_training_count == 1
    )
    resumed_review = await resumed.review_humanization_training(
        "owned-project", skill.name
    )
    assert len(resumed_review.published) == 1
    assert len(resumed.training_store.list_pending("owned-project", skill.name)) == 2


@pytest.mark.asyncio
async def test_humanization_training_stays_pending_until_manual_apply(
    tmp_path: Path,
) -> None:
    service = SkillFileService(str(tmp_path))

    created = await service.create_humanization_skill(
        "project-1",
        HumanizationSkillCreateRequest(
            display_name="自然客服",
            description="让客服回复更像真人交流",
        ),
    )

    assert created.name.startswith("humanization-")
    assert created.display_name == "自然客服"
    assert created.skill_type == "humanization"
    assert created.enabled is False
    assert created.pending_training_count == 0
    assert created.published_version == 1

    pending = await service.add_humanization_training_sample(
        "project-1",
        created.name,
        HumanizationTrainingSampleRequest(
            customer_message="订单 13800138000 怎么还没到？",
            ai_draft="根据物流信息，目前包裹正在派送中。",
            final_reply="包裹正在派送中。",
            source_message_id="msg-1",
        ),
    )

    assert pending.pending_training_count == 1
    skill_before_apply = await service.get_skill("project-1", created.name)
    assert "13800138000" not in skill_before_apply.instructions
    assert "包裹正在派送中" not in skill_before_apply.instructions

    review = await service.review_humanization_training("project-1", created.name)
    sample = review.pending[0].model_copy(
        update={
            "selected": True,
            "change_kind": "expression",
            "rules": ["物流咨询直接说明当前状态。"],
        }
    )
    applied = await service.publish_humanization_training(
        "project-1",
        created.name,
        TrainingPublishRequest(snapshot_id=review.snapshot_id, samples=[sample]),
    )

    assert applied.applied_count == 1
    assert applied.pending_training_count == 0
    assert applied.published_version == 2

    examples = await service.get_file(
        "project-1",
        created.name,
        "references/style-library.json",
    )
    assert "根据物流信息" in examples
    assert "包裹正在派送中" in examples
    assert "13800138000" not in examples
    assert "[手机号]" in examples


@pytest.mark.asyncio
async def test_humanization_skill_can_use_explicit_ascii_name(tmp_path: Path) -> None:
    service = SkillFileService(str(tmp_path))

    created = await service.create_humanization_skill(
        "project-1",
        HumanizationSkillCreateRequest(
            name="friendly-after-sales",
            display_name="售后真人话术",
            description="售后场景的自然表达",
        ),
    )

    assert created.name == "friendly-after-sales"
    assert created.display_name == "售后真人话术"
