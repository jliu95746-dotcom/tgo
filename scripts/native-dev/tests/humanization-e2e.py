"""Opt-in humanization lifecycle and real model checks; never sends customer messages."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "repos/tgo-api"))

from app.schemas.humanization import TrainingPublishRequest  # noqa: E402
from app.services.ai_client import ai_client  # noqa: E402
from app.services.humanization_review_service import analyze_training  # noqa: E402
from app.services.humanization_service import (  # noqa: E402
    context_prompt,
    rewrite_assist_draft,
)


def report(message: str) -> None:
    print(message, flush=True)


async def verify(project_id: str, check_model: bool) -> None:
    suffix = uuid4().hex[:12]
    names = [f"natural-e2e-{suffix}-a", f"natural-e2e-{suffix}-b"]
    created: list[str] = []
    try:
        for name in names:
            detail = await ai_client.create_humanization_skill(
                project_id,
                {
                    "name": name,
                    "display_name": "临时拟人验证",
                    "description": f"isolated-e2e:{suffix}",
                },
            )
            created.append(name)
            assert detail["skill_type"] == "humanization" and detail["enabled"] is False
        report(
            "PASS Chinese display name and two disabled, unbound test skills created"
        )
        expression = {
            "customer_message": "这款有绿色吗？",
            "ai_draft": "抱歉，这款没有绿色。请问您主要在什么场合背，预算大概多少？",
            "final_reply": "这款没有绿色。",
            "source_message_id": f"{suffix}-color",
        }
        await ai_client.add_humanization_training_sample(
            project_id, names[0], expression
        )
        duplicate = await ai_client.add_humanization_training_sample(
            project_id, names[0], expression
        )
        assert duplicate["pending_training_count"] == 1
        await ai_client.add_humanization_training_sample(
            project_id,
            names[0],
            {
                "customer_message": "这款多少钱？",
                "ai_draft": "这款399元。",
                "final_reply": "这款599元。",
                "source_message_id": f"{suffix}-price",
            },
        )
        await ai_client.add_humanization_training_sample(
            project_id,
            names[1],
            {
                **expression,
                "final_reply": "没有绿色哦。",
                "source_message_id": f"{suffix}-other-skill",
            },
        )
        before = await ai_client.match_humanization_context(
            project_id,
            names[0],
            "有没有蓝色？",
            factual_draft="已确认这款没有蓝色。",
        )
        assert before.published_version == 1 and not before.examples
        review = await ai_client.review_humanization_training(project_id, names[0])
        assert len(review.pending) == 2 and not review.published
        report(
            "PASS edits only enter selected pending library; duplicate capture is idempotent"
        )
        if check_model:
            report("RUN real model analysis of expression versus price correction")
            review = await analyze_training(project_id, names[0])
            assert not review.analysis_error, review.analysis_error
            by_source = {sample.source_message_id: sample for sample in review.pending}
            assert by_source[f"{suffix}-color"].change_kind == "expression"
            assert by_source[f"{suffix}-price"].change_kind in {"facts", "mixed"}
            report(
                "PASS real model separates expression edits from changed business facts"
            )
        else:
            # Deterministic lifecycle-only run. --check-model verifies the actual
            # review analyzer instead of supplying these known fixture labels.
            review.pending = [
                sample.model_copy(
                    update={
                        "change_kind": "expression"
                        if sample.source_message_id.endswith("color")
                        else "facts",
                        "selected": sample.source_message_id.endswith("color"),
                        "scene_tags": ["颜色"]
                        if sample.source_message_id.endswith("color")
                        else ["价格"],
                        "rules": ["只问颜色时直接回答，不追加预算或用途追问。"]
                        if sample.source_message_id.endswith("color")
                        else [],
                    }
                )
                for sample in review.pending
            ]
        proposal = TrainingPublishRequest(
            snapshot_id=review.snapshot_id, samples=review.pending
        )
        candidate = await ai_client.match_humanization_context(
            project_id,
            names[0],
            "有没有蓝色？",
            candidate=proposal,
            factual_draft="已确认这款没有蓝色。",
        )
        assert candidate.published_version == 2 and len(candidate.examples) == 1
        unchanged = await ai_client.match_humanization_context(
            project_id,
            names[0],
            "有没有蓝色？",
            factual_draft="已确认这款没有蓝色。",
        )
        assert unchanged.published_version == 1 and not unchanged.examples
        report(
            "PASS preview does not publish; candidate and live version stay separate"
        )
        published = await ai_client.apply_humanization_training(
            project_id, names[0], proposal.model_dump()
        )
        assert published["published_version"] == 2 and published["applied_count"] == 1
        assert published["pending_training_count"] == 0
        live = await ai_client.match_humanization_context(
            project_id,
            names[0],
            "有没有蓝色？",
            factual_draft="已确认这款没有蓝色。",
        )
        assert live.published_version == 2 and len(live.examples) == 1
        assert live.examples[0].source_message_id == expression["source_message_id"]
        other = await ai_client.review_humanization_training(project_id, names[1])
        assert other.published_version == 1 and len(other.pending) == 1
        for query, facts in [("多少钱？", "这款399元。"), ("蓝色有吗？", "未找到蓝色记录，是否有蓝色尚未确认。")]:
            unmatched = await ai_client.match_humanization_context(
                project_id, names[0], query, factual_draft=facts
            )
            assert not unmatched.examples
        try:
            await ai_client.apply_humanization_training(
                project_id, names[0], proposal.model_dump()
            )
        except HTTPException as exc:
            assert exc.status_code == 422
        else:
            raise AssertionError("Stale preview was published twice")
        report(
            "PASS explicit update publishes once; no skill/scenario/fact-state cross-contamination"
        )
        if check_model:
            for label, question, facts, style in [
                ("confirmed color", "这款有蓝色吗？", "已确认这款没有蓝色。", context_prompt(live)),
                (
                    "unknown material",
                    "有红色小羊皮的包吗？",
                    "我查了一下知识库，暂未找到同时满足红色与小羊皮的款式，部分款式的材质信息暂未明确列出，是否有符合条件的款式还不能确认。",
                    "",
                ),
                ("technical topic", "支持 API 对接吗？", "已确认支持 API 对接。", ""),
                ("knowledge operation", "知识库文章怎么编辑？", "打开知识库，选择文章后点击编辑。", ""),
            ]:
                report(f"RUN real model rewrite and independent fact audit: {label}")
                reply = await rewrite_assist_draft(
                    ai_client,
                    project_id=project_id,
                    agent_id=None,
                    customer_message=question,
                    factual_draft=facts,
                    humanization_prompt=style,
                )
                assert reply and len(reply) < 160
                if label == "confirmed color":
                    assert "蓝色" in reply and "绿色" not in reply
                    assert not any(word in reply for word in ["预算", "场合", "抱歉"])
                elif label == "unknown material":
                    assert not any(
                        word in reply
                        for word in ["知识库", "列出", "感谢", "没有货", "查到", "找到", "同时满足"]
                    )
                elif label == "technical topic":
                    assert "API" in reply
                elif label == "knowledge operation":
                    assert "编辑" in reply
                report(f"PASS {label}: {reply}")
    finally:
        for name in created:
            detail = await ai_client.get_skill(project_id, name)
            if detail.get("description") != f"isolated-e2e:{suffix}":
                raise RuntimeError(
                    "Test skill ownership changed; preserved instead of deleting"
                )
            await ai_client.delete_skill(project_id, name)
            report(f"CLEANED own test skill: {name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument(
        "--check-model",
        action="store_true",
        help="Calls the project's configured real model",
    )
    args = parser.parse_args()
    asyncio.run(verify(args.project_id, args.check_model))
