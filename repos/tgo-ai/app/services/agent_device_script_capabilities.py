"""Observe a fixed script without committing the caller transaction."""

import asyncio
from datetime import datetime, timedelta, timezone

from app.exceptions import ConflictError
from app.schemas.agent_device_script_capability import ScriptRuntimeDefinition
from app.schemas.agent_device_script_selection import ScriptSelectionRecord
from app.services.agent_device_script_cases import AgentDeviceScriptCaseService
from app.services.agent_device_script_selection import ScriptSelectionService


class AgentDeviceScriptCapabilityService:
    def __init__(self, cases: AgentDeviceScriptCaseService) -> None:
        self.cases = cases
        self.selections = ScriptSelectionService(cases)

    async def read(
        self,
        expected: ScriptSelectionRecord,
        *,
        require_capacity: bool = True,
    ) -> ScriptRuntimeDefinition:
        expected = ScriptSelectionRecord.model_validate_json(
            expected.model_dump_json()
        )
        try:
            async with asyncio.timeout(10):
                return await self._read(
                    expected, require_capacity=require_capacity
                )
        except TimeoutError:
            raise ConflictError("设备脚本能力核验超时，请稍后重试") from None

    async def _read(
        self,
        expected: ScriptSelectionRecord,
        *,
        require_capacity: bool,
    ) -> ScriptRuntimeDefinition:
        project, actor, agent = (
            expected.project_id,
            expected.configured_by,
            expected.agent_id,
        )
        await self.cases._authorize(project, actor, agent)
        state = await self.selections._state(
            project, actor, agent, await self.selections._row(project, agent)
        )
        if state.status != "selected" or state.selection != expected:
            raise ConflictError("当前固定审批选择已失效，请重新核验")
        trial = await self.cases.trials._row(
            project, actor, agent, report=expected.report_id
        )
        original = self.cases.trials._reference(trial)
        if original.descriptor.effect != "read_only":
            raise ConflictError("员工工具仅支持已审核的只读脚本")
        budget = await self.cases.trials._current(trial)
        # Network I/O cannot make an old local selection valid again.
        state = await self.selections._state(
            project, actor, agent, await self.selections._row(project, agent)
        )
        now = datetime.now(timezone.utc)
        until = min(
            expected.expires_at,
            budget.observed_at + timedelta(seconds=30),
            now + timedelta(seconds=30),
        )
        if (
            state.status != "selected"
            or state.selection != expected
            or until <= now
        ):
            raise ConflictError("核验期间审批或设备已变化")
        if require_capacity and budget.remaining_runs < 1:
            raise ConflictError("设备脚本剩余次数不足")
        return ScriptRuntimeDefinition(
            selection=expected,
            descriptor=original.descriptor,
            remaining_runs=budget.remaining_runs,
            checked_at=now,
            valid_until=until,
        )
