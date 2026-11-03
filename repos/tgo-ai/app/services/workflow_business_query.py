"""Delegate one workflow read to the formal business query protocol."""

from datetime import UTC, datetime
import hashlib
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AuthorizationError, ExternalServiceError
from app.models.agent_business_connection import AgentBusinessConnection
from app.schemas.agent_business_execution import BusinessExecutionCommand
from app.schemas.workflow_business_query import (
    WorkflowBusinessQueryRequest, WorkflowBusinessQueryResult,
)
from app.services.agent_execution_reference import read_reference
from app.services.api_service import api_service_client


class WorkflowBusinessQueryService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def execute(
        self, request: WorkflowBusinessQueryRequest,
    ) -> WorkflowBusinessQueryResult:
        principal = await api_service_client.get_business_request_context(
            request.context
        )
        now = datetime.now(UTC)
        token_digest = hashlib.sha256(
            request.context.context_token.get_secret_value().encode()
        ).hexdigest()
        if (
            principal.project_id != request.project_id
            or principal.visitor_id != request.context.visitor_id
            or principal.request_id != request.context.request_id
            or principal.channel != request.context.channel
            or principal.request_fingerprint
            != request.context.request_fingerprint
            or principal.visitor_session_id != request.visitor_session_id
            or principal.context_digest != token_digest
            or principal.expires_at <= now
            or not any(
                item.connection_id == request.connection_id
                for item in principal.bindings
            )
        ):
            raise AuthorizationError("原客户业务请求与工作流范围不一致")

        binding = await self.db.scalar(select(AgentBusinessConnection).where(
            AgentBusinessConnection.project_id == request.project_id,
            AgentBusinessConnection.agent_id == request.agent_id,
            AgentBusinessConnection.connection_id == request.connection_id,
        ).execution_options(populate_existing=True))
        if (
            binding is None or not binding.enabled
            or binding.project_id != request.project_id
            or binding.agent_id != request.agent_id
            or binding.connection_id != request.connection_id
            or request.operation not in binding.operations
        ):
            raise AuthorizationError("当前员工没有此业务连接的只读操作")

        reference = await read_reference(
            self.db, request.project_id, request.agent_id,
            request.context.channel,
        )
        if (
            reference.project_id != request.project_id
            or reference.agent_id != request.agent_id
            or reference.channel != request.context.channel
            or reference.business_evidence_revision is None
        ):
            raise AuthorizationError("当前员工执行依据无法确认")

        command = BusinessExecutionCommand(
            query_id=uuid4(), reference=reference,
            connection_id=request.connection_id,
            binding_id=binding.id, binding_revision=binding.revision,
            operation=request.operation,
            context_digest=principal.context_digest,
            context=request.context,
            order_no=SecretStr(request.order_no.get_secret_value()),
        )
        receipt = await api_service_client.query_business_execution(command)
        if (
            not receipt.matches(command)
            or not receipt.authority.matches(command)
            or receipt.authority.phase != "after_query"
            or receipt.authority.expires_at <= datetime.now(UTC)
        ):
            raise ExternalServiceError("tgo-api", "业务查询回执无法确认")
        return WorkflowBusinessQueryResult(
            project_id=request.project_id,
            workflow_execution_id=request.workflow_execution_id,
            query_id=receipt.query_id,
            connection_id=request.connection_id,
            operation=request.operation,
            outcome=receipt.outcome,
            summary=receipt.summary,
            error_code=receipt.error_code,
        )
