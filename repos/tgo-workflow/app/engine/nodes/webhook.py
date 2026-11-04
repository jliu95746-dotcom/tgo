from pydantic import JsonValue
from app.engine.nodes.base import BaseNodeExecutor
from app.engine.context import ExecutionContext
from app.engine.nodes.registry import register_node
from app.services.execution_snapshot import copy_json_object


@register_node("webhook")
class WebhookNodeExecutor(BaseNodeExecutor):
    async def execute(
        self, context: ExecutionContext
    ) -> tuple[dict[str, JsonValue], str | None]:
        data = context.get_variable(f"{self.reference_key}.data")
        if not isinstance(data, dict):
            raise ValueError("外部通知缺少已核验的业务数据。")
        return {"body": copy_json_object(data)}, None
