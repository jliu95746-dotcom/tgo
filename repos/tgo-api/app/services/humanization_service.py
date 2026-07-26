"""Resolve published humanization skills for chat and assist drafting."""

from __future__ import annotations

from app.services.ai_client import ai_client


APPROVED_EXAMPLES_PATH = "references/approved-examples.md"


async def get_humanization_skill_prompt(
    project_id: str,
    skill_name: str,
) -> str:
    detail = await ai_client.get_skill(project_id, skill_name)
    if detail.get("skill_type") != "humanization":
        raise ValueError(f"Skill '{skill_name}' is not a humanization skill")

    instructions = str(detail.get("instructions") or "").strip()
    references = detail.get("references")
    approved_examples = ""
    if isinstance(references, list) and APPROVED_EXAMPLES_PATH in references:
        approved_examples = (
            await ai_client.get_skill_file(
                project_id,
                skill_name,
                APPROVED_EXAMPLES_PATH,
            )
        ).strip()

    prompt_parts = [instructions]
    if approved_examples:
        # Recent approved examples are the most representative while keeping
        # the customer-service system prompt bounded.
        prompt_parts.append(approved_examples[-12000:])
    return "\n\n".join(part for part in prompt_parts if part)


def append_humanization_prompt(
    system_message: str | None,
    humanization_prompt: str,
) -> str:
    prefix = (system_message or "").strip()
    section = (
        "以下是当前会话明确启用的拟人技能。只调整表达方式，不得改变业务事实：\n\n"
        f"{humanization_prompt.strip()}"
    )
    return f"{prefix}\n\n{section}" if prefix else section
