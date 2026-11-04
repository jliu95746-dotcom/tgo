"""Seal provenance without changing signatures of existing manual receipts."""

import hmac
from collections.abc import Awaitable, Callable

from pydantic import ValidationError

from app.exceptions import ConflictError
from app.models.agent_device_script_run import AgentDeviceScriptRun
from app.schemas.agent_device_script_source import ScriptRunSource
from app.services.agent_trial_fingerprint import private_digest

ScriptSourceGuard = Callable[[ScriptRunSource], Awaitable[None]]


def _digest(row: AgentDeviceScriptRun, source: ScriptRunSource) -> str:
    return private_digest({
        "purpose": "employee-script-runtime-source-v1",
        "owner": {
            name: str(getattr(row, name)) for name in (
                "id", "project_id", "actor_id", "agent_id",
                "request_id", "report_id", "review_id",
            )
        },
        "source": source.model_dump(mode="json"),
    })


def seal_source(
    row: AgentDeviceScriptRun, source: ScriptRunSource | None,
) -> None:
    if source is not None:
        row.agent_execution_id = source.agent_execution_id
        row.source_json = source.model_dump_json()
        row.source_digest = _digest(row, source)


def read_source(row: AgentDeviceScriptRun) -> ScriptRunSource | None:
    if all(value is None for value in (
        row.agent_execution_id, row.source_json, row.source_digest,
    )):
        return None
    try:
        source = ScriptRunSource.model_validate_json(row.source_json or "")
        if (
            source.agent_execution_id != row.agent_execution_id
            or source.reference.project_id != row.project_id
            or source.reference.agent_id != row.agent_id
            or not hmac.compare_digest(
                _digest(row, source), row.source_digest or "",
            )
        ):
            raise ValueError("Runtime source changed")
        return source
    except (ValueError, ValidationError):
        raise ConflictError("原员工运行来源无法核验") from None
