"""Script config validation through the shared AI transport and identity."""

from uuid import UUID

from fastapi import HTTPException

from app.schemas.agent_device_script_binding import (
    AgentDeviceScriptRecord,
    AgentDeviceScriptState,
    AgentDeviceScriptWrite,
)
from app.services.agent_activation_policy_client import PolicyTransport, decode


def validate_record(
    result: AgentDeviceScriptRecord, project: UUID, employee: UUID
) -> None:
    if (
        not set(AgentDeviceScriptRecord.model_fields).issubset(
            result.model_fields_set
        )
        or result.project_id != project
        or result.agent_id != employee
        or result.can_enable is not False
    ):
        raise HTTPException(502, "员工脚本配置范围不一致")


async def read(
    client: PolicyTransport, project: UUID, actor: UUID, employee: UUID
) -> AgentDeviceScriptState:
    response = await client._make_request(
        "GET",
        f"/api/v1/agents/{employee}/device-script-binding",
        extra_headers=client._trial_headers(project, actor),
    )
    result = decode(response, AgentDeviceScriptState)
    if result.project_id != project or result.agent_id != employee:
        raise HTTPException(502, "员工脚本配置范围不一致")
    if result.binding is not None:
        validate_record(result.binding, project, employee)
    return result


async def save(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    employee: UUID,
    command: AgentDeviceScriptWrite,
) -> AgentDeviceScriptRecord:
    response = await client._make_request(
        "PUT",
        f"/api/v1/agents/{employee}/device-script-binding",
        json_data=command.model_dump(mode="json"),
        extra_headers=client._trial_headers(project, actor),
    )
    result = decode(response, AgentDeviceScriptRecord)
    validate_record(result, project, employee)
    if (
        result.enabled != command.enabled
        or result.configured_by != actor
        or result.revision != command.expected_revision + 1
        or command.enabled
        and result.device_id != command.device_id
        or command.enabled
        and result.policy_digest != command.expected_policy_digest
    ):
        raise HTTPException(502, "员工脚本配置结果与原请求不一致")
    return result


async def recover(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    employee: UUID,
    request: UUID,
) -> AgentDeviceScriptRecord:
    response = await client._make_request(
        "GET",
        f"/api/v1/agents/{employee}/device-script-binding/requests/{request}",
        extra_headers=client._trial_headers(project, actor),
    )
    result = decode(response, AgentDeviceScriptRecord)
    validate_record(result, project, employee)
    if result.configured_by != actor:
        raise HTTPException(502, "脚本配置请求身份不一致")
    return result
