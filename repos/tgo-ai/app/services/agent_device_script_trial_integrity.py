"""Bind the query snapshot to its original trial and signing key."""

from pydantic import JsonValue

from app.models.agent_device_script_trial import AgentDeviceScriptTrial
from app.schemas.agent_device_script_trials import ScriptTrialReference
from app.services.agent_trial_fingerprint import private_digest


def trial_reference_digest(
    row: AgentDeviceScriptTrial, ref: ScriptTrialReference
) -> str:
    identity: dict[str, JsonValue] = {
        "report_id": str(row.id),
        "project_id": str(row.project_id),
        "actor_id": str(row.actor_id),
        "agent_id": str(row.agent_id),
        "request_id": str(row.request_id),
        "binding_id": str(row.binding_id),
        "binding_revision": row.binding_revision,
        "command_digest": row.command_digest,
        "expected_digest": row.expected_digest,
        "expires_at": row.expires_at.isoformat(),
    }
    return private_digest(
        {
            "purpose": "script-trial-reference-v1",
            "identity": identity,
            "reference": ref.model_dump(mode="json"),
        }
    )
