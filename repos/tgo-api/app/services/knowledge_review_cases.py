"""Knowledge cases derive from the existing versioned template owner."""
from fastapi import HTTPException
from typing import Literal

from app.schemas.agent_trial_reviews import KnowledgeReviewCase
from app.services.scenario_templates import get_template


def list_cases() -> list[KnowledgeReviewCase]:
    cases = []
    template_ids: tuple[Literal["knowledge", "aftersales"], ...] = (
        "knowledge",
        "aftersales",
    )
    for template_id in template_ids:
        template = get_template("agents", template_id, 1)
        for index, question in enumerate(template.test_questions):
            cases.append(
                KnowledgeReviewCase(
                    id=f"{template.id}.v{template.version}.{index+1}",
                    template_id=template_id,
                    template_version=template.version,
                    question=question,
                    expected_result="safe_refusal"
                    if index == 2
                    else "grounded_answer",
                )
            )
    return cases


def get_case(identifier: str) -> KnowledgeReviewCase:
    for case in list_cases():
        if case.id == identifier:
            return case
    raise HTTPException(404, "未找到当前岗位测试用例，请刷新模板")
