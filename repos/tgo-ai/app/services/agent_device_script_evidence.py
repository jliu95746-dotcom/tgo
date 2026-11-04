"""Actual result retention is separate from execution and enablement."""

import hmac
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError
from app.models.agent_device_script_evidence import AgentDeviceScriptEvidence
from app.models.agent_device_script_trial import AgentDeviceScriptTrial
from app.schemas.agent_device_script_cases import (
    ScriptResultValues,
    ScriptTrialEvidence,
)
from app.schemas.device_script_execution import ScriptExecutionRead
from app.services.agent_activation import lock_account_agents
from app.services.agent_trial_fingerprint import private_digest
from app.services.script_evidence_vault import ScriptEvidenceVault

if TYPE_CHECKING:
    from app.services.agent_device_script_cases import (
        AgentDeviceScriptCaseService,
    )
    from app.services.agent_device_script_trials import (
        AgentDeviceScriptTrialService,
    )


async def _request(
    db: AsyncSession,
    project: UUID,
    actor: UUID,
    request: UUID,
) -> AgentDeviceScriptEvidence | None:
    row: AgentDeviceScriptEvidence | None = await db.scalar(
        select(AgentDeviceScriptEvidence)
        .where(
            AgentDeviceScriptEvidence.project_id == project,
            AgentDeviceScriptEvidence.actor_id == actor,
            AgentDeviceScriptEvidence.request_id == request,
        )
        .execution_options(populate_existing=True)
    )
    return row


async def admit_case_request(
    db: AsyncSession,
    project: UUID,
    actor: UUID,
    agent: UUID,
    request: UUID,
    digest: str,
    evidence_id: UUID | None,
) -> None:
    row = await _request(db, project, actor, request)
    if row is None and evidence_id is None:
        return
    if (
        row is None
        or row.id != evidence_id
        or row.agent_id != agent
        or not hmac.compare_digest(row.command_digest, digest)
    ):
        raise ConflictError("原测试已固定用例，不能替换输入或改为临时测试")


async def attach_case_evidence(
    db: AsyncSession,
    project: UUID,
    actor: UUID,
    agent: UUID,
    request: UUID,
    report: UUID,
) -> None:
    row = await _request(db, project, actor, request)
    if row is None:
        return
    trial = await db.get(AgentDeviceScriptTrial, report)
    if (
        trial is None
        or trial.project_id != project
        or trial.actor_id != actor
        or trial.agent_id != agent
        or trial.request_id != request
        or row.agent_id != agent
        or row.report_id not in {None, report}
        or not hmac.compare_digest(row.command_digest, trial.command_digest)
    ):
        raise ConflictError("用例与原执行报告不一致")
    row.report_id = report


async def capture_case_result(
    trials: "AgentDeviceScriptTrialService",
    trial: AgentDeviceScriptTrial,
    receipt: ScriptExecutionRead,
) -> None:
    if receipt.status != "succeeded" or not receipt.result_available:
        return
    await lock_account_agents(trials.db, trial.project_id)
    row = await _request(
        trials.db, trial.project_id, trial.actor_id, trial.request_id
    )
    if row is None:
        await trials.db.commit()
        return
    if row.report_id != trial.id or row.agent_id != trial.agent_id:
        raise ConflictError("结果不属于原用例执行")
    if row.expires_at <= datetime.now(timezone.utc) or row.cleared_at:
        await trials.db.commit()
        return
    payload = ScriptResultValues(
        report_id=trial.id,
        reference_digest=trial.reference_digest,
        case_digest=row.case_digest,
        fields=receipt.fields,
    )
    digest = private_digest(dict(payload.fields))
    if row.captured_at is not None:
        if row.result_digest != digest:
            raise ConflictError("原结果证据已经固定，不能覆盖")
        await trials.db.commit()
        return
    row.ciphertext = ScriptEvidenceVault().encrypt(
        row.project_id,
        row.actor_id,
        row.agent_id,
        row.id,
        "result",
        payload.model_dump_json(),
    )
    row.result_digest = digest
    row.captured_at = datetime.now(timezone.utc)
    await trials.db.commit()


async def read_evidence(
    cases: "AgentDeviceScriptCaseService",
    project: UUID,
    actor: UUID,
    agent: UUID,
    report: UUID,
) -> ScriptTrialEvidence:
    await cases._authorize(project, actor, agent)
    trial = await cases.trials._row(project, actor, agent, report=report)
    return await read_trial_evidence(cases, trial)


async def read_trial_evidence(
    cases: "AgentDeviceScriptCaseService", trial: AgentDeviceScriptTrial
) -> ScriptTrialEvidence:
    """Read an already authorized report without calling a remote service."""
    project, actor, agent, report = (
        trial.project_id, trial.actor_id, trial.agent_id, trial.id
    )
    cases.trials._reference(trial)
    row = await _request(cases.db, project, actor, trial.request_id)
    base = dict(
        project_id=project, actor_id=actor, agent_id=agent, report_id=report
    )
    if row is None:
        return ScriptTrialEvidence(
            project_id=project,
            actor_id=actor,
            agent_id=agent,
            report_id=report,
            status="not_retained",
            case=None,
            values=None,
            result_fields=None,
            captured_at=None,
            expires_at=None,
        )
    if row.agent_id != agent or row.report_id != report:
        raise ConflictError("原报告的证据范围无法确认")
    version = await cases._version(project, agent, row.case_version_id)
    if row.case_digest != version.case_digest:
        raise ConflictError("原用例版本已变化")
    metadata = await cases._record(version)
    outcome = "pending" if trial.status == "unknown" else "unavailable"
    values, result = None, None
    if (
        row.expires_at <= datetime.now(timezone.utc)
        or row.cleared_at
        or (metadata.status == "expired")
    ):
        outcome = "expired"
    elif row.ciphertext is not None and row.captured_at is not None:
        raw = ScriptEvidenceVault().decrypt(
            project, actor, agent, row.id, "result", row.ciphertext
        )
        try:
            payload = ScriptResultValues.model_validate_json(raw)
            digest = private_digest(dict(payload.fields))
            if (
                payload.report_id != report
                or payload.reference_digest != trial.reference_digest
                or payload.case_digest != version.case_digest
                or row.result_digest != digest
                or trial.result_digest is not None
                and trial.result_digest != digest
            ):
                raise ValueError("Stored result changed")
            values, result = cases._values(version), payload.fields
            outcome = "available"
        except ValueError:
            raise ConflictError("结果证据未通过完整性核验") from None
    return ScriptTrialEvidence.model_validate(
        {
            **base,
            "status": outcome,
            "case": metadata,
            "values": values,
            "result_fields": result,
            "captured_at": row.captured_at,
            "expires_at": row.expires_at,
        }
    )
