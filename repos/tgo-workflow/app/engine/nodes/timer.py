from pydantic import JsonValue
from app.engine.nodes.base import BaseNodeExecutor
from app.engine.context import ExecutionContext
from app.engine.nodes.registry import register_node
from datetime import datetime, timezone


@register_node("timer")
class TimerNodeExecutor(BaseNodeExecutor):
    async def execute(
        self, context: ExecutionContext
    ) -> tuple[dict[str, JsonValue], None]:
        reference = str(self.config.get("reference_key", "timer"))
        scheduled = context.get_variable(reference + ".scheduled_at")
        timestamp = (
            scheduled
            if isinstance(scheduled, str)
            else datetime.now(timezone.utc).isoformat()
        )
        outputs: dict[str, JsonValue] = {
            "timestamp": timestamp,
            "scheduled_at": timestamp,
        }
        return outputs, None
