"""Agent-level human handoff tool for requesting human support."""

from __future__ import annotations

from typing import Any, Optional

from agno.tools import Function

from .base import EventClient, ToolContext


def create_handoff_tool(
    *,
    agent_id: str,
    session_id: str | None,
    user_id: str | None,
    project_id: str | None = None,
    request_id: str | None = None,
) -> Function:
    """Create an agent-level tool that requests human support."""
    ctx = ToolContext(agent_id, session_id, user_id, project_id, request_id)
    client = EventClient(ctx)

    error_messages = {
        "not_configured": "抱歉，当前无法为您发起人工服务请求，我们已记录该问题并会尽快处理。请稍后再试或直接联系客服。",
        "api_error": "人工服务请求状态未确认，请联系客服核对，暂勿重复提交。",
        "http_error": "网络异常，人工服务请求状态未知，请联系客服核对，暂勿重复提交。",
        "unexpected_error": "人工服务请求状态未知，请联系客服核对，暂勿重复提交。",
    }

    async def request_human_support(
        *,
        reason: str,
        urgency: str = "normal",
        channel: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """Request human support via manual_service.request event."""
        result = await client.post_event(
            "manual_service.request",
            {
                "reason": reason,
                "urgency": urgency,
                "channel": channel,
                "metadata": metadata,
            },
            error_messages=error_messages,
        )

        if not result.success:
            return result.message
        if result.notification_status != "submitted":
            return (
                f"[handoff_requested] 人工转接已记录，编号 {result.request_id}。"
                "通知状态未知，请人工核对，暂勿重复提交。"
            )
        return (
            f"[handoff_requested] 人工转接已记录，编号 {result.request_id}。" "已提交客服通知。"
        )

    return Function(
        name="request_human_support",
        description=(
            "当用户明确要求人工或你判断需要人工介入时调用此工具，用于发起人工支持流程。" "请在参数中简要说明原因与紧急程度。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "触发人工介入的原因（必填）"},
                "urgency": {
                    "type": "string",
                    "description": "紧急程度：low|normal|high|urgent（默认 normal）",
                },
                "channel": {
                    "type": "string",
                    "description": "期望的人工渠道（如 phone/wechat/email/ticket 等，可选）",
                },
                "metadata": {"type": "object", "description": "其他上下文字段（可选）"},
            },
            "required": ["reason"],
        },
        entrypoint=request_human_support,
        skip_entrypoint_processing=True,
    )
