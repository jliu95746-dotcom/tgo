"""The fork's branch execution is coordinated by WorkflowExecutor."""

from pydantic import JsonValue

from app.engine.context import ExecutionContext
from app.engine.nodes.base import BaseNodeExecutor
from app.engine.nodes.registry import register_node


@register_node("parallel")
class ParallelNodeExecutor(BaseNodeExecutor):
    async def execute(
        self,
        context: ExecutionContext,
    ) -> tuple[dict[str, JsonValue], str | None]:
        return {"branch_count": self.config.get("branches", 2)}, None
