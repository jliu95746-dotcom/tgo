"""Expose only operation inputs to Agno; the server owns customer identity and authority."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
import re
from uuid import uuid4

from agno.tools.function import Function
from pydantic import SecretStr

from app.models.internal import AgentExecutionContext
from app.schemas.agent_business_connections import (
    AgentBusinessConnectionRecord,
    BusinessOperation,
)
from app.schemas.agent_business_execution import (
    BusinessExecutionCommand,
    BusinessExecutionReceipt,
)
from app.schemas.business_execution_context import BusinessContextCheck
from app.schemas.knowledge_evidence import BusinessToolEvidence, KnowledgeEvidence
from app.services.agent_execution_scope import ExecutionRevoked, current_execution_scope
from app.services.api_service import api_service_client

Query = Callable[[BusinessExecutionCommand], Awaitable[BusinessExecutionReceipt]]
ORDER_NUMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{3,63}$")


def _tool(
    context: AgentExecutionContext,
    binding: AgentBusinessConnectionRecord,
    operation: BusinessOperation,
    query: Query,
) -> Function:
    action = {
        "order_query": "order",
        "logistics_query": "logistics",
        "customer_profile_query": "profile",
        "membership_query": "member",
    }[operation]
    name = f"employee_business_{action}_{binding.connection_id.hex}"

    async def execute(order_no: str | None = None) -> str:
        scope = current_execution_scope()
        reference, principal, token = (
            context.execution_reference,
            context.business_principal,
            context.business_context_token,
        )
        if (
            scope is None
            or reference is None
            or scope.reference != reference
            or principal is None
            or token is None
            or principal.expires_at <= datetime.now(UTC)
        ):
            if scope is not None:
                scope.revoke()
            raise ExecutionRevoked()
        await scope.check()
        if operation in {"order_query", "logistics_query"}:
            if order_no is None or not ORDER_NUMBER.fullmatch(order_no.strip()):
                return "订单号格式不完整，请客户核对后重新提供；本次没有查询结果。"
            secret_order = SecretStr(order_no.strip())
        else:
            secret_order = None
        command = BusinessExecutionCommand.model_validate({
            "query_id": uuid4(),
            "reference": reference,
            "connection_id": binding.connection_id,
            "binding_id": binding.id,
            "binding_revision": binding.revision,
            "operation": operation,
            "context_digest": principal.context_digest,
            "order_no": secret_order,
            "context": BusinessContextCheck(
                **principal.model_dump(
                    include=set(BusinessContextCheck.model_fields) - {"context_token"}
                ),
                context_token=token,
            ),
        })
        try:
            result = await query(command)
            if not result.matches(
                command
            ) or result.authority.expires_at <= datetime.now(UTC):
                raise ValueError("Original query receipt changed")
            await scope.check()
        except Exception:
            scope.revoke()
            raise ExecutionRevoked() from None
        if context.knowledge_evidence is None:
            context.knowledge_evidence = KnowledgeEvidence(
                status="skipped",
                retrieved_at=datetime.now(UTC),
                project_id=context.project_id,
                channel=principal.channel.value,
            )
        passed = result.outcome == "passed" and result.summary is not None
        content = (
            result.summary.model_dump_json(exclude_none=True)
            if passed and result.summary
            else "业务查询未成功，无法确认结果，请核对信息或转人工。"
        )
        context.knowledge_evidence.tool_results.append(
            BusinessToolEvidence(
                name=name, content=content, success=passed,
                query_id=command.query_id, operation=operation,
            )
        )
        return str(content)

    descriptions = {
        "order_query": "查询当前已验证客户的订单状态",
        "logistics_query": "查询当前已验证客户的物流状态",
        "customer_profile_query": "查询当前已验证客户的服务分层和标签，不接收客户编号",
        "membership_query": "查询当前已验证客户的会员等级和积分，不接收客户编号",
    }
    needs_order = operation in {"order_query", "logistics_query"}
    return Function(
        name=name,
        description=descriptions[operation],
        parameters={
            "type": "object",
            "properties": {
                "order_no": {"type": "string", "description": "客户提供的订单号"}
            } if needs_order else {},
            "required": ["order_no"] if needs_order else [],
            "additionalProperties": False,
        },
        entrypoint=execute,
    )


def build_business_tools(
    context: AgentExecutionContext,
    *,
    query: Query = api_service_client.query_business_execution,
) -> list[Function]:
    reference, principal = context.execution_reference, context.business_principal
    if (
        context.disable_tools
        or context.response_purpose == "expression"
        or reference is None
        or reference.business_evidence_revision is None
        or principal is None
        or context.business_context_token is None
        or principal.expires_at <= datetime.now(UTC)
        or reference.project_id != principal.project_id
        or reference.channel != principal.channel
    ):
        return []
    tools: list[Function] = []
    for binding in context.agent.business_connections:
        if (
            binding.enabled
            and binding.agent_id == reference.agent_id
            and binding.project_id == reference.project_id
            and any(
                row.connection_id == binding.connection_id for row in principal.bindings
            )
        ):
            for operation in binding.operations:
                tools.append(_tool(context, binding, operation, query))
    return tools
