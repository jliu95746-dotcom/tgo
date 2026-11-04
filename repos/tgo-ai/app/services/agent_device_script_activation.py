"""Observe the selected approval without reserving or committing a run."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError
from app.models.agent_activation_policy import AgentActivationPolicy
from app.models.internal import Agent
from app.schemas.agent_device_script_capability import ScriptRuntimeDefinition
from app.schemas.agent_device_script_selection import ScriptSelectionRecord
from app.schemas.knowledge import KnowledgeChannel

ScriptDefinitionReader = Callable[
    [ScriptSelectionRecord], Awaitable[ScriptRuntimeDefinition]
]


async def read_script_definition(
    db: AsyncSession, selection: ScriptSelectionRecord
) -> ScriptRuntimeDefinition:
    from app.services.agent_device_script_capabilities import (
        AgentDeviceScriptCapabilityService,
    )
    from app.services.agent_device_script_cases import (
        AgentDeviceScriptCaseService,
    )
    from app.services.agent_device_script_trials import (
        AgentDeviceScriptTrialService,
    )

    return await AgentDeviceScriptCapabilityService(
        AgentDeviceScriptCaseService(AgentDeviceScriptTrialService(db))
    ).read(selection, require_capacity=False)


async def script_activation_evidence(
    project: UUID,
    actor: UUID,
    agent: Agent,
    policies: list[AgentActivationPolicy],
    reader: ScriptDefinitionReader,
) -> ScriptRuntimeDefinition | None:
    if not policies or any(
        row.template_id != "assistant"
        or row.channel != KnowledgeChannel.INTERNAL.value
        for row in policies
    ):
        return None
    selected = agent.device_script_selection
    if (
        any(row.updated_by != actor for row in policies)
        or selected is None or not selected.enabled
        or selected.project_id != project or selected.agent_id != agent.id
        or selected.configured_by != actor
    ):
        raise ConflictError("内部业务助理需要选择本人已评审的脚本审批")
    selected = ScriptSelectionRecord.model_validate_json(
        selected.model_dump_json()
    )
    definition = ScriptRuntimeDefinition.model_validate_json(
        (await reader(selected)).model_dump_json()
    )
    now = datetime.now(UTC)
    if (
        definition.selection != selected
        or not -5 <= (now - definition.checked_at).total_seconds() <= 30
        or definition.valid_until <= now
    ):
        raise ConflictError("脚本审批或运行条件已变化，请重新核验")
    return definition
