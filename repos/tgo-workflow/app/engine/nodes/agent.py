from pydantic import JsonValue, ValidationError
from app.engine.nodes.base import BaseNodeExecutor
from app.engine.context import ExecutionContext
from app.engine.nodes.registry import register_node
from app.integrations.ai_client import AIClient
from app.core.logging import logger
from app.schemas.agent_runtime import AgentRuntimeRequest

@register_node("agent")
class AgentNodeExecutor(BaseNodeExecutor):
    async def execute(self, context: ExecutionContext) -> tuple[dict[str, JsonValue], str | None]:
        agent_id = self.config.get("agent_id")
        input_mapping = self.config.get("input_mapping", {})
        
        # Resolve inputs using mapping
        resolved_inputs: dict[str, JsonValue] = {}
        for key, template in input_mapping.items():
            resolved_inputs[key] = context.resolve_variables(template)
            
        message = resolved_inputs.get("message") or str(resolved_inputs)
        if not context.project_id:
            raise RuntimeError("工作流缺少企业范围，不能调用 AI 员工")
        try:
            payload = AgentRuntimeRequest.model_validate({
                "agent_id": agent_id, "message": message,
                "session_id": resolved_inputs.get("session_id"),
                "expected_device_id": self.config.get("expected_device_id"),
                "expected_device_policy_digest": self.config.get("expected_device_policy_digest"),
            })
        except ValidationError:
            raise RuntimeError("AI 员工输入字段无效，请检查员工编号和输入映射") from None
        
        try:
            data = await AIClient.run_agent(
                context.project_id, payload,
                parent_execution_id=context.execution_id,
                workflow_lineage=context.workflow_lineage,
                workflow_node_id=self.node_id,
            )
            if data.execution_id is None:
                raise RuntimeError("AI 员工执行回执缺少运行编号")
            return {
                "text": data.content,
                "agent_execution_id": str(data.execution_id),
            }, None
            
        except Exception as e:
            logger.error("Agent execution failed: %s", type(e).__name__)
            raise

