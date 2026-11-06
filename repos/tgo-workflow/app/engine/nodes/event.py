from typing import Any, Dict, Optional, Tuple
from app.engine.nodes.base import BaseNodeExecutor
from app.engine.context import ExecutionContext
from app.engine.nodes.registry import register_node


@register_node("event")
class EventNodeExecutor(BaseNodeExecutor):
    async def execute(
        self, context: ExecutionContext
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        # Event node can inject event data
        outputs = {"data": context.get_variable(f"{self.reference_key}.data")}
        return outputs, None
