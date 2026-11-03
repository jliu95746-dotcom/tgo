"""A passive approval gate; persistence is handled by the execution owner."""

from pydantic import JsonValue

from app.engine.context import ExecutionContext
from app.engine.nodes.base import BaseNodeExecutor
from app.engine.nodes.registry import register_node


@register_node("approval")
class ApprovalNodeExecutor(BaseNodeExecutor):
    async def execute(
        self, context: ExecutionContext,
    ) -> tuple[dict[str, JsonValue], str | None]:
        # A returned value never grants execution; WorkflowExecutor requires a
        # durable pause callback and refuses to proceed without it.
        return {"requested": True}, None
