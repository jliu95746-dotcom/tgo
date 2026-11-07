"""Authorize only the exact serving employee's configured read operation."""

import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AuthorizationError, ConflictError
from app.schemas.agent_business_execution import (
    BusinessExecutionCheck,
    BusinessExecutionAuthority,
)
from app.services.agent_activation_policies import AgentActivationPolicyService
from app.services.agent_business_trials import AgentBusinessTrialService
from app.services.agent_business_reviews import AgentBusinessReviewService
from app.services.agent_business_capabilities import (
    AgentBusinessCapabilityService,
)
from app.services.agent_execution_reference import read_reference
from app.services.api_service import api_service_client


class AgentBusinessExecutionService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        trials: AgentBusinessTrialService | None = None,
        capabilities: AgentBusinessCapabilityService | None = None,
    ):
        self.db = db
        self.trials = trials or AgentBusinessTrialService(db)
        self.capabilities = capabilities or AgentBusinessCapabilityService(
            db, AgentBusinessReviewService(db, self.trials)
        )

    async def authorize(
        self, check: BusinessExecutionCheck
    ) -> BusinessExecutionAuthority:
        try:
            async with asyncio.timeout(25):
                return await self._authorize(check)
        except TimeoutError:
            raise ConflictError("正式业务授权核验超时，请重试或转人工") from None

    async def _authorize(
        self, check: BusinessExecutionCheck
    ) -> BusinessExecutionAuthority:
        command = check.command
        reference = command.reference
        project, agent, channel = (
            reference.project_id,
            reference.agent_id,
            reference.channel,
        )
        if channel is None:
            raise AuthorizationError("当前服务渠道无法确认")
        current = await read_reference(self.db, project, agent, channel)
        if current != reference:
            raise ConflictError("原员工执行或业务评审依据已变化")
        policies = [
            row
            for row in await AgentActivationPolicyService(self.db)._rows(
                project, agent
            )
            if row.channel == channel.value and row.deleted_at is None
        ]
        if len(policies) != 1 or policies[0].template_id != "logistics":
            raise AuthorizationError("当前渠道的业务岗位无法确认")
        actor = policies[0].updated_by
        binding, owner, fingerprint = await self.trials._current(
            project,
            actor,
            agent,
            command.connection_id,
            command.operation,
            require_paused=False,
        )
        if (
            binding.id != command.binding_id
            or binding.revision != command.binding_revision
            or fingerprint != reference.configuration_fingerprint
        ):
            raise ConflictError("员工的连接或允许操作已变化")
        coverage = await self.capabilities.read(project, actor, agent)
        operations = [
            row
            for row in coverage.capabilities
            if row.binding_id == binding.id
            and row.connection_id == command.connection_id
            and row.operation == command.operation
        ]
        if len(operations) != 1:
            raise ConflictError("当前业务操作没有有效评审")
        operation = operations[0]
        if (
            operation.status != "passed"
            or operation.issues
            or operation.report_id is None
            or operation.review_id is None
            or operation.valid_until is None
        ):
            raise ConflictError("当前业务操作尚未通过评审或已失效")
        principal = await api_service_client.get_business_request_context(
            command.context
        )
        customer = next(
            (
                row
                for row in principal.bindings
                if row.connection_id == command.connection_id
            ),
            None,
        )
        if (
            principal.context_digest != command.context_digest
            or customer is None
            or customer.connection_configuration_revision
            != owner.configuration_revision
        ):
            raise AuthorizationError("原客户与当前连接身份不一致")
        # Bind to this same run after awaited owner calls, never a newly valid
        # one.
        if await read_reference(self.db, project, agent, channel) != reference:
            raise ConflictError("核验期间原员工或业务评审已变化")
        config_test = next(
            (
                row
                for row in owner.configuration_tests
                if row.operation == command.operation
            ),
            None,
        )
        if (
            config_test is None
            or config_test.report_id is None
            or config_test.valid_until is None
        ):
            raise ConflictError("连接配置测试已失效")
        checked_at = datetime.now(UTC)
        expires_at = min(
            operation.valid_until,
            config_test.valid_until,
            principal.expires_at,
        )
        if expires_at <= checked_at:
            raise ConflictError("原正式查询授权已到期")
        return BusinessExecutionAuthority.model_validate(
            {
                **command.identity(),
                "actor_id": actor,
                "phase": check.phase,
                "connection_revision": owner.revision,
                "configuration_revision": owner.configuration_revision,
                "authorization_revision": owner.authorization_revision,
                "resource_revision": owner.resource_revision,
                "report_id": operation.report_id,
                "review_id": operation.review_id,
                "configuration_test_id": config_test.report_id,
                "checked_at": checked_at,
                "expires_at": expires_at,
            }
        )
