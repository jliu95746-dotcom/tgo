"""Read an owned order or logistics connection through the AI authority chain."""

from uuid import UUID

from pydantic import JsonValue, SecretStr

from app.engine.context import ExecutionContext
from app.engine.nodes.base import BaseNodeExecutor
from app.engine.nodes.registry import register_node
from app.integrations.ai_client import AIClient
from app.schemas.business_query import BusinessQueryCommand


@register_node("business_query")
class BusinessQueryNodeExecutor(BaseNodeExecutor):
    async def execute(self, context: ExecutionContext) -> tuple[dict[str, JsonValue], None]:
        business_context = context.business_context
        if (
            business_context is None or context.project_id is None
            or context.execution_id is None or context.employee_agent_id is None
            or str(business_context.project_id) != context.project_id
        ):
            raise PermissionError("受控业务查询需要有效客户会话与员工身份")
        if self.config.get("order_variable") != "input.order_no":
            raise ValueError("受控业务查询订单字段未配置")
        order_no = context.get_variable("input.order_no")
        if not isinstance(order_no, str):
            raise ValueError("受控业务查询缺少订单号")
        command = BusinessQueryCommand(
            project_id=business_context.project_id,
            agent_id=UUID(context.employee_agent_id),
            workflow_execution_id=UUID(context.execution_id),
            connection_id=UUID(self.config["connection_id"]),
            operation=self.config["operation"],
            visitor_session_id=business_context.visitor_session_id,
            order_no=SecretStr(order_no), context=business_context,
        )
        receipt = await AIClient.query_business(command)
        summary = receipt.summary
        return {
            "outcome": receipt.outcome,
            "status": summary.status if summary is not None else None,
            "carrier": summary.carrier if summary is not None else None,
            "tracking_no_masked": (
                summary.tracking_no_masked if summary is not None else None
            ),
            "error_code": receipt.error_code,
            "query_id": str(receipt.query_id),
            "connection_id": str(receipt.connection_id),
            "operation": receipt.operation,
        }, None
