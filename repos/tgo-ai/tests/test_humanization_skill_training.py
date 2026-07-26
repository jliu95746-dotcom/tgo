from pathlib import Path

import pytest

from app.schemas.skill import (
    HumanizationSkillCreateRequest,
    HumanizationTrainingSampleRequest,
)
from app.services.skill_file_service import SkillFileService


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
            ai_draft="我正在为您查询，请稍等。",
            final_reply="我帮您看一下物流，稍等我一下。",
            source_message_id="msg-1",
        ),
    )

    assert pending.pending_training_count == 1
    skill_before_apply = await service.get_skill("project-1", created.name)
    assert "13800138000" not in skill_before_apply.instructions
    assert "我帮您看一下物流" not in skill_before_apply.instructions

    applied = await service.apply_humanization_training(
        "project-1", created.name
    )

    assert applied.applied_count == 1
    assert applied.pending_training_count == 0
    assert applied.published_version == 2

    examples = await service.get_file(
        "project-1",
        created.name,
        "references/approved-examples.md",
    )
    assert "我正在为您查询" in examples
    assert "我帮您看一下物流" in examples
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
