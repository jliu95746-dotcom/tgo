"""Internal runtime adapter; activation pins remain a separate gate."""

from typing import NoReturn
from uuid import UUID, uuid5

from pydantic import ValidationError

from app.exceptions import ConflictError, ExternalServiceError, NotFoundError
from app.models.internal import AgentExecutionContext
from app.schemas.agent_device_script_runs import ScriptRunStart, ScriptRunView
from app.schemas.agent_device_script_source import (
    ScriptRunSource,
    ScriptRuntimeApproval,
)
from app.schemas.device_script_execution import BusinessValue
from app.schemas.knowledge import KnowledgeChannel
from app.services.agent_activation_policies import AgentActivationPolicyService
from app.services.agent_device_script_cases import AgentDeviceScriptCaseService
from app.services.agent_device_script_runs import AgentDeviceScriptRuns
from app.services.agent_device_script_selection import ScriptSelectionService
from app.services.agent_business_evidence_revision import (
    business_evidence_revision,
)
from app.services.agent_trial_fingerprint import configuration_fingerprint
from app.services.agent_execution_reference import policy_revision
from app.services.agent_execution_scope import (
    ExecutionRevoked,
    current_execution_scope,
)


class AgentDeviceScriptRuntime:
    """One trusted invocation; LLM arguments contain business parameters only.

    The caller must supply a persisted approval selection and a stable runtime
    invocation UUID. This adapter does not choose the latest approval, activate
    an employee, or expose scripts to external customer channels.
    """

    def __init__(
        self,
        cases: AgentDeviceScriptCaseService,
        context: AgentExecutionContext,
        approval: ScriptRuntimeApproval,
        invocation_id: UUID,
    ) -> None:
        self.cases, self.context = cases, context
        self.approval = ScriptRuntimeApproval.model_validate_json(
            approval.model_dump_json(),
        )
        self.scope = current_execution_scope()
        self.source = (
            ScriptRunSource(
                agent_execution_id=context.execution_id,
                reference=context.execution_reference,
                workflow_execution_id=context.parent_workflow_execution_id,
            )
            if context.execution_reference is not None
            and context.execution_reference.channel is not None
            else None
        )
        self.request_id = uuid5(
            context.execution_id,
            f"employee-device-script-v1:{invocation_id}",
        )
        self.runs = AgentDeviceScriptRuns(cases, source_guard=self._guard)
        self.selections = ScriptSelectionService(cases)

    def _deny(self) -> NoReturn:
        if self.scope is not None:
            self.scope.revoke()
        raise ExecutionRevoked()

    async def _guard(self, source: ScriptRunSource) -> None:
        context, scope = self.context, self.scope
        if (
            self.source != source
            or scope is None
            or current_execution_scope() is not scope
            or scope.reference != source.reference
            or context.execution_reference != source.reference
            or context.execution_id != source.agent_execution_id
            or context.parent_workflow_execution_id
            != source.workflow_execution_id
            or context.agent.id != source.reference.agent_id
            or context.project_id != str(source.reference.project_id)
            or context.knowledge_channel != source.reference.channel
            or context.disable_tools
            or context.response_purpose == "expression"
            or source.reference.channel != KnowledgeChannel.INTERNAL
        ):
            self._deny()
        await scope.check()
        policies = AgentActivationPolicyService(self.cases.db)
        project, agent = source.reference.project_id, source.reference.agent_id
        state = await self.selections._state(
            project,
            self.approval.actor_id,
            agent,
            await self.selections._row(project, agent),
        )
        selection = state.selection
        if (
            state.status != "selected"
            or selection is None
            or selection != context.agent.device_script_selection
            or selection.id != self.approval.selection_id
            or selection.revision != self.approval.selection_revision
            or selection.selection_digest != self.approval.selection_digest
            or selection.report_id != self.approval.report_id
            or selection.review_id != self.approval.review_id
            or configuration_fingerprint(context.agent)
            != source.reference.configuration_fingerprint
            or await business_evidence_revision(
                self.cases.db,
                project,
                self.approval.actor_id,
                agent,
            )
            != source.reference.business_evidence_revision
        ):
            self._deny()
        rows = await policies._rows(project, agent)
        selected = [
            row
            for row in rows
            if row.deleted_at is None
            and row.channel == KnowledgeChannel.INTERNAL.value
        ]
        binding = await self.cases.trials.bindings._binding(project, agent)
        if (
            not await policies.is_agent_active(project, agent)
            or len(selected) != 1
            or selected[0].updated_by != self.approval.actor_id
            or policy_revision(rows, source.reference.channel)
            != source.reference.policy_revision
            or binding is None
            or not binding.enabled
            or binding.configured_by != self.approval.actor_id
        ):
            self._deny()

    async def execute(
        self,
        parameters: dict[str, BusinessValue],
    ) -> ScriptRunView:
        if self.source is None:
            self._deny()
        source = self.source
        try:
            command = ScriptRunStart(
                request_id=self.request_id,
                report_id=self.approval.report_id,
                review_id=self.approval.review_id,
                parameters=parameters,
            )
            await self._guard(source)
            result = await self.runs.start(
                source.reference.project_id,
                self.approval.actor_id,
                source.reference.agent_id,
                command,
                source=source,
            )
            if result.result_fields is None and result.status in {
                "unknown",
                "succeeded",
            }:
                await self._guard(source)
                result = await self.runs.get(
                    source.reference.project_id,
                    self.approval.actor_id,
                    source.reference.agent_id,
                    self.request_id,
                )
            await self._guard(source)
            return result
        except (
            ConflictError,
            ExternalServiceError,
            NotFoundError,
            ValueError,
            ValidationError,
        ):
            self._deny()
