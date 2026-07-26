"""Resolve published humanization skills for chat and assist drafting."""

from __future__ import annotations

from typing import Optional

from app.services.ai_client import AIServiceClient, ai_client


APPROVED_EXAMPLES_PATH = "references/approved-examples.md"

ASSIST_FACT_GATHERING_PROMPT = (
    "你是人工客服的业务答复助手。先根据客户当前消息和可用工具确认业务事实。"
    "需要查询时直接调用工具，不要先说‘我查一下’、‘请稍等’或类似过程话术。"
    "查询完成后只输出事实完整的答复正文，不描述思考、查询、工具调用或内部工作过程。"
    "不确定的业务事实要明确说明，不能猜测。"
)

ASSIST_REWRITE_PROMPT = (
    "你负责把一条已经完成查询的业务答复，改成真人客服会直接发给客户的话。"
    "这一步禁止查询和调用工具，只能改写现有内容。"
    "必须保留原答复里的产品、价格、库存、政策等业务事实，不得补充、删改或猜测。"
    "删掉‘我先查一下’、‘感谢您的耐心等待’、‘根据知识库’等过程播报和模板客套话。"
    "直接回应客户，语气自然、简短、口语化；通常用两到四句话。"
    "除非内容确实很长，不要写标题、总结、分点清单或 Markdown 强调。"
    "只输出可直接发送给客户的回复正文。"
)


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


async def rewrite_assist_draft(
    client: AIServiceClient,
    *,
    project_id: str,
    agent_id: Optional[str],
    customer_message: str,
    factual_draft: str,
    humanization_prompt: str = "",
) -> str:
    """Rewrite a factual assist draft in a tool-free, memory-free second pass."""
    system_message = ASSIST_REWRITE_PROMPT
    if humanization_prompt.strip():
        system_message = append_humanization_prompt(
            system_message,
            humanization_prompt,
        )

    message = (
        "客户原话：\n"
        f"{customer_message.strip()}\n\n"
        "已经确认事实的业务答复：\n"
        f"{factual_draft.strip()}"
    )
    result = await client.run_supervisor_agent(
        message=message,
        project_id=project_id,
        agent_id=agent_id,
        session_id=None,
        system_message=system_message,
        enable_memory=False,
        disable_tools=True,
        markdown=False,
        temperature=0.4,
    )
    content = result.get("content")
    rewritten = content.strip() if isinstance(content, str) else ""
    if not rewritten:
        raise ValueError("AI service returned an empty rewritten assist draft")
    return rewritten
