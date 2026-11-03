from pydantic import JsonValue, TypeAdapter
from app.engine.nodes.base import BaseNodeExecutor
from app.engine.context import ExecutionContext
from app.engine.nodes.registry import register_node
from app.integrations.ai_client import AIClient
from app.core.logging import logger
from app.schemas.tool import ToolExecuteResponse

@register_node("tool")
class ToolNodeExecutor(BaseNodeExecutor):
    async def execute(self, context: ExecutionContext) -> tuple[dict[str, JsonValue], None]:
        tool_id = self.config.get("tool_id")
        if not isinstance(tool_id, str) or not tool_id.strip():
            raise ValueError("请先选择业务工具。")
        if not context.project_id:
            raise ValueError("缺少企业上下文，无法调用业务工具。")
            
        input_mapping = self.config.get("input_mapping", {})
        
        # Resolve inputs using mapping
        if not isinstance(input_mapping, dict):
            raise ValueError("业务工具的输入映射格式不正确。")
        resolved_inputs: dict[str, JsonValue] = {}
        for key, template in input_mapping.items():
            resolved_inputs[key] = context.resolve_variables(template)
            
        try:
            execution_data = await AIClient.execute_tool(
                project_id=context.project_id,
                tool_id=tool_id,
                inputs=resolved_inputs
            )
            
            if not isinstance(execution_data, ToolExecuteResponse) or not execution_data.success:
                raise RuntimeError("Tool reported failure or invalid status")
            result: JsonValue = TypeAdapter(JsonValue).validate_python(execution_data.output_data)
            return {"result": result}, None
            
        except Exception as error:
            # The second return value selects a branch; it must never carry an error.
            # Upstream errors can contain credentials, payloads, or private URLs.
            logger.warning("Workflow tool call failed: %s", type(error).__name__)
            raise RuntimeError("业务工具调用失败，请检查工具配置或稍后重试。") from None

