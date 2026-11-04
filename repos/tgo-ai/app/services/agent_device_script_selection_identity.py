"""Local selection snapshots are comparison inputs, never execution grants."""

import hmac
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError, NotFoundError
from app.models.agent_device_script_selection import AgentDeviceScriptSelection
from app.schemas.agent_device_script_selection import ScriptSelectionRecord
from app.services.agent_trial_fingerprint import private_digest


def selection_record(row: AgentDeviceScriptSelection) -> ScriptSelectionRecord:
    return ScriptSelectionRecord.model_validate(
        {
            name: getattr(row, name)
            for name in ScriptSelectionRecord.model_fields
            if name not in {"scope", "can_enable"}
        }
    )


def selection_signature(value: ScriptSelectionRecord) -> str:
    return private_digest(
        {
            "purpose": "employee-script-fixed-selection-v1",
            "record": value.model_dump(
                mode="json", exclude={"selection_digest"}
            ),
        }
    )


def verified_selection(
    row: AgentDeviceScriptSelection,
) -> ScriptSelectionRecord:
    try:
        value = selection_record(row)
        if row.deleted_at is not None or not hmac.compare_digest(
            selection_signature(value),
            value.selection_digest,
        ):
            raise ValueError("Selection changed")
        return value
    except (ValueError, ValidationError):
        raise ConflictError("固定审批选择无法核验") from None


async def selection_row(
    db: AsyncSession,
    project: UUID,
    agent: UUID,
) -> AgentDeviceScriptSelection | None:
    row: AgentDeviceScriptSelection | None = await db.scalar(
        select(AgentDeviceScriptSelection)
        .where(
            AgentDeviceScriptSelection.project_id == project,
            AgentDeviceScriptSelection.agent_id == agent,
        )
        .execution_options(populate_existing=True)
    )
    if row is not None:
        verified_selection(row)
    return row


async def selection_snapshot(
    db: AsyncSession,
    project: UUID,
    agent: UUID,
) -> ScriptSelectionRecord | None:
    row = await selection_row(db, project, agent)
    return verified_selection(row) if row is not None else None


async def selection_evidence_revision(
    db: AsyncSession,
    project: UUID,
    actor: UUID,
    agent: UUID,
) -> str | None:
    row = await selection_row(db, project, agent)
    if row is None:
        return None
    # Deferred imports keep configuration loading independent of trial runtime.
    from app.services.agent_device_script_cases import (
        AgentDeviceScriptCaseService,
    )
    from app.services.agent_device_script_selection import (
        ScriptSelectionService,
    )
    from app.services.agent_device_script_trials import (
        AgentDeviceScriptTrialService,
    )

    service = ScriptSelectionService(
        AgentDeviceScriptCaseService(AgentDeviceScriptTrialService(db)),
    )
    state = await service._state(project, actor, agent, row)
    review = None
    if state.selection is not None and state.selection.enabled:
        try:
            trial = await service.cases.trials._row(
                project,
                actor,
                agent,
                report=state.selection.report_id,
            )
            review = await service.reviews._view(
                trial,
                await service.reviews._review(trial),
            )
        except (ConflictError, NotFoundError):
            pass
    # No remote call or execution. _state/_view check retained local proof;
    # only fixed metadata and review state enter this opaque digest.
    return private_digest(
        {
            "purpose": "employee-script-selection-evidence-v1",
            "project": str(project),
            "actor": str(actor),
            "agent": str(agent),
            "selection": state.model_dump(mode="json"),
            "review": review.model_dump(mode="json")
            if review is not None
            else None,
        }
    )
