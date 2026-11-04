"""Fresh sessions for script definitions and approved invocations."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.exceptions import ConflictError
from app.models.internal import AgentExecutionContext
from app.schemas.agent_device_script_capability import ScriptRuntimeDefinition
from app.schemas.agent_device_script_runs import ScriptRunView
from app.schemas.agent_device_script_source import ScriptRuntimeApproval
from app.schemas.device_script_execution import BusinessValue
from app.services.agent_device_script_bindings import ConfigurationActorOwner
from app.services.agent_device_script_capabilities import (
    AgentDeviceScriptCapabilityService,
)
from app.services.agent_device_script_cases import AgentDeviceScriptCaseService
from app.services.agent_device_script_runtime import AgentDeviceScriptRuntime
from app.services.agent_device_script_trials import (
    AgentDeviceScriptTrialService,
    ScriptTrialDevice,
)
from app.services.api_service import api_service_client
from app.services.device_control_client import device_control_client


class AgentDeviceScriptToolRuntime:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        actor: ConfigurationActorOwner = api_service_client,
        device: ScriptTrialDevice = device_control_client,
    ) -> None:
        self.sessions, self.actor, self.device = sessions, actor, device

    def _cases(self, db: AsyncSession) -> AgentDeviceScriptCaseService:
        return AgentDeviceScriptCaseService(
            AgentDeviceScriptTrialService(db, self.actor, self.device)
        )

    async def definition(
        self, context: AgentExecutionContext
    ) -> ScriptRuntimeDefinition:
        selected = context.agent.device_script_selection
        if selected is None:
            raise ConflictError("员工尚未选择有效的脚本审批")
        async with self.sessions() as db:
            return await AgentDeviceScriptCapabilityService(
                self._cases(db)
            ).read(selected)

    async def execute(
        self,
        context: AgentExecutionContext,
        definition: ScriptRuntimeDefinition,
        invocation: UUID,
        parameters: dict[str, BusinessValue],
    ) -> ScriptRunView:
        async with self.sessions() as db:
            cases = self._cases(db)
            # New requests still require capacity in the durable run gate.
            # Keep the original receipt readable after the last run is used.
            current = await AgentDeviceScriptCapabilityService(cases).read(
                definition.selection,
                require_capacity=False,
            )
            if current.descriptor != definition.descriptor:
                raise ConflictError("已评审的业务脚本定义发生变化")
            selected = definition.selection
            approval = ScriptRuntimeApproval(
                actor_id=selected.configured_by,
                report_id=selected.report_id,
                review_id=selected.review_id,
                selection_id=selected.id,
                selection_revision=selected.revision,
                selection_digest=selected.selection_digest,
            )
            return await AgentDeviceScriptRuntime(
                cases, context, approval, invocation
            ).execute(parameters)
