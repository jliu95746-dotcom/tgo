from typing import Any, Dict, Optional, Tuple
from app.engine.nodes.base import BaseNodeExecutor
from app.engine.context import ExecutionContext
from app.engine.nodes.registry import register_node


@register_node("answer")
class AnswerNodeExecutor(BaseNodeExecutor):
    async def execute(
        self, context: ExecutionContext
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        output_type = self.config.get("output_type", "variable")
        result = None

        if output_type == "variable":
            var_path = self.config.get("output_variable")
            if var_path:
                result = context.get_variable(var_path)
        elif output_type == "template":
            template = self.config.get("output_template", "")
            result = context.resolve_template(template)
        elif output_type == "structured":
            structure = self.config.get("output_structure", [])
            result = {}
            for field in structure:
                key = field["key"]
                value_template = field["value"]
                result[key] = context.resolve_variables(value_template)

        return {"result": result}, None
