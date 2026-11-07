"Validate employee business configuration through the existing AI \
transport."
from uuid import UUID

from fastapi import HTTPException

from app.schemas.agent_business_connections import (
    AgentBusinessConnectionRecord,
    AgentBusinessConnections,
    AgentBusinessConnectionWrite,
)
from app.services.agent_activation_policy_client import PolicyTransport, decode


def validate_record(
    record: AgentBusinessConnectionRecord, project: UUID, agent: UUID
) -> None:
    if (
        not set(AgentBusinessConnectionRecord.model_fields).issubset(
            record.model_fields_set
        )
        or record.project_id != project
        or record.agent_id != agent
        or record.can_enable is not False
    ):
        raise HTTPException(502, "员工业务配置范围不一致")


async def read(
    client: PolicyTransport, project: UUID, actor: UUID, agent: UUID
) -> AgentBusinessConnections:
    response = await client._make_request(
        "GET",
        f"/api/v1/agents/{agent}/business-connections",
        extra_headers=client._trial_headers(project, actor),
    )
    result = decode(response, AgentBusinessConnections)
    if (
        not set(AgentBusinessConnections.model_fields).issubset(
            result.model_fields_set
        )
        or result.project_id != project
        or result.agent_id != agent
        or result.can_enable is not False
    ):
        raise HTTPException(502, "员工业务配置范围不一致")
    for record in result.bindings:
        validate_record(record, project, agent)
    return result


async def save(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
    connection: UUID,
    command: AgentBusinessConnectionWrite,
) -> AgentBusinessConnectionRecord:
    response = await client._make_request(
        "PUT",
        f"/api/v1/agents/{agent}/business-connections/{connection}",
        json_data=command.model_dump(mode="json"),
        extra_headers=client._trial_headers(project, actor),
    )
    result = decode(response, AgentBusinessConnectionRecord)
    validate_record(result, project, agent)
    if (
        result.connection_id != connection
        or result.operations != command.operations
        or result.enabled != command.enabled
        or result.configured_by != actor
        or result.revision != command.expected_revision + 1
    ):
        raise HTTPException(502, "员工业务配置结果与原请求不一致")
    return result


async def recover(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
    request: UUID,
) -> AgentBusinessConnectionRecord:
    response = await client._make_request(
        "GET",
        f"/api/v1/agents/{agent}/business-connections/requests/{request}",
        extra_headers=client._trial_headers(project, actor),
    )
    result = decode(response, AgentBusinessConnectionRecord)
    validate_record(result, project, agent)
    if result.configured_by != actor:
        raise HTTPException(502, "配置请求身份不一致")
    return result
