"""Reserve the exact saved version before the existing trial can dispatch."""

import hmac
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select

from app.config import settings
from app.exceptions import ConflictError
from app.models.agent_device_script_evidence import AgentDeviceScriptEvidence
from app.models.agent_device_script_trial import AgentDeviceScriptTrial
from app.models.agent_device_script_trial_request import (
    AgentDeviceScriptTrialRequest,
)
from app.schemas.agent_device_script_requests import ScriptTrialControl
from app.schemas.agent_device_script_trials import (
    ScriptTrialStart,
    ScriptTrialReport,
)
from app.services.agent_activation import lock_account_agents
from app.services.agent_trial_fingerprint import private_digest

if TYPE_CHECKING:
    from app.services.agent_device_script_cases import (
        AgentDeviceScriptCaseService,
    )


async def _reservation(
    cases: "AgentDeviceScriptCaseService",
    project: UUID,
    actor: UUID,
    agent: UUID,
    version: UUID,
    command: ScriptTrialControl,
) -> AgentDeviceScriptEvidence | None:
    row: AgentDeviceScriptEvidence | None = await cases.db.scalar(
        select(AgentDeviceScriptEvidence)
        .where(
            AgentDeviceScriptEvidence.project_id == project,
            AgentDeviceScriptEvidence.actor_id == actor,
            AgentDeviceScriptEvidence.request_id == command.request_id,
        )
        .execution_options(populate_existing=True)
    )
    if row is not None:
        case = await cases._version(project, agent, version)
        if (
            row.agent_id != agent
            or row.case_version_id != version
            or case.binding_id != command.binding_id
            or case.binding_revision != command.binding_revision
            or not hmac.compare_digest(row.case_digest, case.case_digest)
        ):
            raise ConflictError("原测试编号的用例版本或范围不一致")
    return row


async def run_case(
    cases: "AgentDeviceScriptCaseService",
    project: UUID,
    actor: UUID,
    agent: UUID,
    version: UUID,
    command: ScriptTrialControl,
) -> ScriptTrialReport:
    await cases._authorize(project, actor, agent)
    old = await _reservation(cases, project, actor, agent, version, command)
    if old is not None:
        if old.report_id is None:
            raise ConflictError("原请求已记录，请查询或取消，不重新派发")
        return await cases.trials.get(project, actor, agent, old.report_id)
    body = await cases.get(project, actor, agent, version)
    if body.case.status != "current" or (
        body.case.binding_id != command.binding_id
        or body.case.binding_revision != command.binding_revision
    ):
        raise ConflictError("用例或脚本绑定已变化，请重新选择")
    request = ScriptTrialStart(
        **command.model_dump(), **body.values.model_dump()
    )
    digest = private_digest(
        {"agent_id": str(agent), "command": request.model_dump(mode="json")}
    )
    await lock_account_agents(cases.db, project)
    try:
        old = await _reservation(
            cases, project, actor, agent, version, command
        )
        if old is not None:
            report_id = old.report_id
            await cases.db.commit()
            if report_id is None:
                raise ConflictError("原请求正在准备，请查询或取消")
            return await cases.trials.get(project, actor, agent, report_id)
        case = await cases._version(project, agent, version)
        employee = await cases.trials.bindings._agent(project, agent)
        current = await cases._record(case)
        if employee.is_active or current.status != "current":
            raise ConflictError("员工或用例版本已变化")
        cases._values(case)  # Recheck immutable content under the same lock.
        for model in (AgentDeviceScriptTrialRequest, AgentDeviceScriptTrial):
            if (
                await cases.db.scalar(
                    select(model.id).where(
                        model.project_id == project,
                        model.actor_id == actor,
                        model.request_id == command.request_id,
                    )
                )
                is not None
            ):
                raise ConflictError("原编号已用于其他测试或已取消")
        now = datetime.now(timezone.utc)
        evidence = AgentDeviceScriptEvidence(
            id=uuid4(),
            project_id=project,
            actor_id=actor,
            agent_id=agent,
            request_id=command.request_id,
            case_version_id=case.id,
            case_digest=case.case_digest,
            command_digest=digest,
            created_at=now,
            expires_at=min(
                case.expires_at,
                now
                + timedelta(days=settings.script_trial_body_retention_days),
            ),
        )
        cases.db.add(evidence)
        await cases.db.commit()
    except BaseException:
        await cases.db.rollback()
        raise
    return await cases.trials.start(
        project, actor, agent, request, evidence_id=evidence.id
    )
