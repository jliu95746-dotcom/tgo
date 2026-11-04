"""Use the existing AI transport for fixed approval configuration."""

from uuid import UUID

from fastapi import HTTPException

from app.schemas.agent_device_script_selection import (
    ScriptSelectionRecord,
    ScriptSelectionState,
    ScriptSelectionWrite,
)
from app.services.agent_activation_policy_client import PolicyTransport
from app.services.agent_device_script_trial_client import _case_call


def _root(agent: UUID) -> str:
    return f"/api/v1/agents/{agent}/device-script-selection"


def _owned(
    result: ScriptSelectionRecord,
    project: UUID,
    actor: UUID,
    agent: UUID,
    request: UUID,
) -> None:
    if (
        result.project_id != project
        or result.agent_id != agent
        or result.configured_by != actor
        or result.request_id != request
    ):
        raise HTTPException(502, "审批选择回执与原请求范围不一致")


async def read_selection(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
) -> ScriptSelectionState:
    result = await _case_call(
        client,
        project,
        actor,
        agent,
        "GET",
        _root(agent),
        ScriptSelectionState,
    )
    if (
        result.project_id != project
        or result.actor_id != actor
        or result.agent_id != agent
    ):
        raise HTTPException(502, "审批选择状态与当前身份不一致")
    return result


async def save_selection(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
    command: ScriptSelectionWrite,
) -> ScriptSelectionRecord:
    result = await _case_call(
        client,
        project,
        actor,
        agent,
        "POST",
        _root(agent),
        ScriptSelectionRecord,
        command=command,
    )
    _owned(result, project, actor, agent, command.request_id)
    if (
        result.revision != command.expected_revision + 1
        or result.enabled != command.enabled
        or command.enabled
        and (
            result.report_id != command.report_id
            or result.review_id != command.review_id
        )
    ):
        raise HTTPException(502, "审批选择回执与保存内容不一致")
    return result


async def selection_request(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
    request: UUID,
) -> ScriptSelectionRecord:
    result = await _case_call(
        client,
        project,
        actor,
        agent,
        "GET",
        f"{_root(agent)}/requests/{request}",
        ScriptSelectionRecord,
    )
    _owned(result, project, actor, agent, request)
    return result
