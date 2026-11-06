from typing import Any, Dict, Optional, Tuple
from app.engine.nodes.base import BaseNodeExecutor
from app.engine.context import ExecutionContext
from app.engine.nodes.registry import register_node


@register_node("input")
class InputNodeExecutor(BaseNodeExecutor):
    async def execute(
        self, context: ExecutionContext
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        input_vars = self.config.get("input_variables", [])
        outputs = {}

        for var in input_vars:
            name = var["name"]
            val = context.get_variable(f"{self.reference_key}.{name}")
            outputs[name] = val

        return outputs, None
