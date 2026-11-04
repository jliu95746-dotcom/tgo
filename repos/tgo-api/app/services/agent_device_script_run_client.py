"""Approved business runs through the existing trusted AI transport."""

from typing import Literal
from uuid import UUID

from fastapi import HTTPException

from app.schemas.agent_device_script_runs import (
    ScriptRunControl,
    ScriptRunStart,
    ScriptRunView,
)
from app.services.agent_activation_policy_client import PolicyTransport
from app.services.agent_device_script_trial_client import _case_call


async def run_call(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
    request: UUID,
    operation: Literal["start", "read", "cancel"],
    command: ScriptRunControl | ScriptRunStart | None = None,
) -> ScriptRunView:
    path = f"/api/v1/agents/{agent}/device-script-runs"
    if operation != "start":
        path += f"/requests/{request}"
    if operation == "cancel":
        path += "/cancel"
    result = await _case_call(
        client,
        project,
        actor,
        agent,
        "GET" if operation == "read" else "POST",
        path,
        ScriptRunView,
        command=command,
        timeout=160,
    )
    if (
        result.project_id != project
        or result.actor_id != actor
        or result.agent_id != agent
        or result.request_id != request
        or command is not None
        and (
            result.report_id != command.report_id
            or result.review_id != command.review_id
        )
    ):
        raise HTTPException(502, "业务执行回执与当前身份或原请求不一致")
    return result
