"Existing AI transport with strict scope and immutable-command receipt \
validation."
from uuid import UUID

from fastapi import HTTPException

from app.schemas.agent_activation_requests import (
    AgentActivationReceipt,
    AgentActivationRequest,
)
from app.services.agent_activation_policy_client import PolicyTransport, decode


def verify(
    receipt: AgentActivationReceipt,
    project: UUID,
    actor: UUID,
    agent: UUID,
    key: UUID,
) -> AgentActivationReceipt:
    if (
        receipt.project_id != project
        or receipt.requested_by != actor
        or receipt.agent_id != agent
        or receipt.request_key != key
    ):
        raise HTTPException(502, "启用原请求回执范围不一致")
    if not set(AgentActivationReceipt.model_fields).issubset(
        receipt.model_fields_set
    ):
        raise HTTPException(502, "启用原请求回执不完整")
    return receipt


async def submit(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
    command: AgentActivationRequest,
) -> AgentActivationReceipt:
    response = await client._make_request(
        "POST",
        f"/api/v1/agents/{agent}/activation-requests",
        json_data=command.model_dump(mode="json"),
        extra_headers=client._trial_headers(project, actor),
        timeout=35,
    )
    receipt = verify(
        decode(response, AgentActivationReceipt),
        project,
        actor,
        agent,
        command.request_key,
    )
    if (
        receipt.status == "not_found"
        or receipt.command is None
        or not _same_intent(receipt.command, command)
    ):
        raise HTTPException(502, "启用结果与原确认内容不一致，请查询原请求")
    return receipt


def _same_intent(
    left: AgentActivationRequest, right: AgentActivationRequest
) -> bool:
    # The owning AI service also treats channel order as immaterial to this
    # key.
    ordered_left = left.expected.model_copy(
        update={
            "channels": sorted(
                left.expected.channels, key=lambda row: row.channel
            )
        }
    )
    ordered_right = right.expected.model_copy(
        update={
            "channels": sorted(
                right.expected.channels, key=lambda row: row.channel
            )
        }
    )
    return (
        left.request_key == right.request_key and ordered_left == ordered_right
    )


async def lookup(
    client: PolicyTransport, project: UUID, actor: UUID, agent: UUID, key: UUID
) -> AgentActivationReceipt:
    response = await client._make_request(
        "GET",
        f"/api/v1/agents/{agent}/activation-requests/{key}",
        extra_headers=client._trial_headers(project, actor),
        timeout=10,
    )
    return verify(
        decode(response, AgentActivationReceipt), project, actor, agent, key
    )
