"""Agent-level tool to add tags to users."""

from __future__ import annotations

from typing import Any, Optional

from agno.tools import Function

from .base import EventClient, ToolContext


ALLOWED_USER_TAGS: dict[str, str] = {
    "vip_customer": "重要客户",
    "high_intent": "高意向",
    "price_sensitive": "价格敏感",
    "technical_support": "技术支持",
    "new_customer": "新客户",
    "returning_customer": "回访客户",
    "complaint_risk": "投诉风险",
    "needs_human": "需要人工",
    "after_sales": "售后服务",
}

USER_TAG_ALIASES: dict[str, str] = {
    "vip": "vip_customer",
    "high_intent_customer": "high_intent",
    "tech_support": "technical_support",
    "returning": "returning_customer",
}


def _canonical_tag_name(value: str) -> str | None:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    canonical = USER_TAG_ALIASES.get(normalized, normalized)
    return canonical if canonical in ALLOWED_USER_TAGS else None


def create_user_tag_tool(
    *,
    agent_id: str,
    session_id: str | None,
    user_id: str | None,
    project_id: str | None = None,
    request_id: str | None = None,
) -> Function:
    """Create an agent-level tool that adds tags to users."""
    ctx = ToolContext(agent_id, session_id, user_id, project_id, request_id)
    client = EventClient(ctx)

    error_messages = {
        "not_configured": "抱歉，当前无法为用户添加标签，我们已记录该问题并会尽快处理。请稍后再试或直接联系客服。",
        "api_error": "抱歉，用户标签添加未能成功提交。请稍后重试或联系技术支持。",
        "http_error": "抱歉，网络异常导致用户标签添加未能提交。请稍后重试或联系技术支持。",
        "unexpected_error": "抱歉，出现异常，暂时无法为用户添加标签。请稍后重试或联系技术支持。",
    }

    async def add_user_tags(
        *,
        tags: list[dict[str, str]],
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """Add tags to user via user_tag.add event.

        Args:
            tags: List of tag objects, each with 'name' (English, required) and 'name_zh' (Chinese, optional)
            metadata: Optional additional context
        """
        if not tags:
            return "请至少提供一个标签。"

        # Validate and normalize tags
        normalized_tags: list[dict[str, str]] = []
        seen_names: set[str] = set()
        for tag in tags:
            if not isinstance(tag, dict):
                return "标签格式错误，每个标签应包含 name 字段。"

            name = tag.get("name", "").strip()
            if not name:
                return "标签的 name 字段不能为空。"

            canonical_name = _canonical_tag_name(name)
            if canonical_name is None:
                allowed = ", ".join(ALLOWED_USER_TAGS)
                return f"不支持的标签：{name}。可用标签：{allowed}。"
            if canonical_name in seen_names:
                continue
            seen_names.add(canonical_name)

            normalized_tags.append(
                {
                    "name": canonical_name,
                    "name_zh": ALLOWED_USER_TAGS[canonical_name],
                }
            )

        result = await client.post_event(
            "user_tag.add",
            {"tags": normalized_tags, "metadata": metadata or {"source": "ai_analysis"}},
            error_messages=error_messages,
        )

        if not result.success:
            return result.message

        tag_names = ", ".join(t["name"] for t in normalized_tags)
        return f"已为用户添加标签：{tag_names}。"

    return Function(
        name="add_user_tags",
        description=(
            "仅当对话中有明确证据时，为用户添加受控标签。"
            "name 只能使用：vip_customer、high_intent、price_sensitive、"
            "technical_support、new_customer、returning_customer、"
            "complaint_risk、needs_human、after_sales。不得临时发明标签。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "description": "标签列表，每个标签包含 name（英文名，必填）和 name_zh（中文名，建议提供）",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "enum": list(ALLOWED_USER_TAGS),
                                "description": "受控标签英文名（必填）",
                            },
                            "name_zh": {"type": "string", "description": "标签中文名（建议提供）"},
                        },
                        "required": ["name"],
                    },
                },
                "metadata": {"type": "object", "description": "其他上下文字段（可选）"},
            },
            "required": ["tags"],
        },
        entrypoint=add_user_tags,
        skip_entrypoint_processing=True,
    )
