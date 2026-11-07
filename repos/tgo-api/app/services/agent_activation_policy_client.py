"""Policy response validation using the existing AI client's transport."""

from typing import Protocol, TypeVar
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, JsonValue, ValidationError

from app.schemas.agent_activation import (
    AgentActivationPolicies,
    AgentActivationPolicyChange,
    AgentActivationPolicyRecord,
)
from app.schemas.knowledge import KnowledgeChannel
from app.schemas.agent_activation_readiness import AgentActivationReadiness
from app.services.ai_transport import QueryValue

ResponseT = TypeVar("ResponseT", bound=BaseModel)


class PolicyTransport(Protocol):
    def _trial_headers(
        self, project_id: UUID, staff_id: UUID
    ) -> dict[str, str]:
        ...

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        *,
        json_data: dict[str, JsonValue] | None = None,
        params: Mapping[str, QueryValue] | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        ...


def decode(response: httpx.Response, schema: type[ResponseT]) -> ResponseT:
    if not response.is_success:
        raise HTTPException(response.status_code, "员工岗位配置暂不可用，请重新读取后重试")
    try:
        return schema.model_validate_json(response.content)
    except ValidationError:
        raise HTTPException(502, "员工岗位配置服务返回了无效结果") from None


async def read(
    client: PolicyTransport, project: UUID, actor: UUID, agent: UUID
) -> AgentActivationPolicies:
    response = await client._make_request(
        "GET",
        f"/api/v1/agents/{agent}/activation-policies",
        extra_headers=client._trial_headers(project, actor),
    )
    result = decode(response, AgentActivationPolicies)
    if result.project_id != project or result.agent_id != agent:
        raise HTTPException(502, "员工岗位配置范围不一致")
    return result


async def readiness(
    client: PolicyTransport, project: UUID, actor: UUID, agent: UUID
) -> AgentActivationReadiness:
    response = await client._make_request(
        "GET",
        f"/api/v1/agents/{agent}/activation-readiness",
        extra_headers=client._trial_headers(project, actor),
        timeout=20,
    )
    result = decode(response, AgentActivationReadiness)
    if (
        result.project_id != project
        or result.agent_id != agent
        or result.checked_by != actor
    ):
        raise HTTPException(502, "员工启用检查范围不一致")
    now = datetime.now(UTC)
    age = (now - result.checked_at).total_seconds()
    if not -30 <= age <= 60 or (
        result.can_enable
        and any(
            row.valid_until is None or row.valid_until <= now
            for row in result.channels
        )
    ):
        raise HTTPException(502, "员工启用检查结果已失效，请重新检查")
    return result


async def save(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
    channel: KnowledgeChannel,
    command: AgentActivationPolicyChange,
) -> AgentActivationPolicyRecord:
    if channel != command.selection.channel:
        raise HTTPException(422, "请求渠道与岗位配置不一致")
    response = await client._make_request(
        "PUT",
        (
            f'/api/v1/agents/'
            f'{agent}'
            f'/activation-policies/'
            f'{command.selection.channel.value}'
        ),
        json_data=command.model_dump(mode="json"),
        extra_headers=client._trial_headers(project, actor),
    )
    result = decode(response, AgentActivationPolicyRecord)
    if (
        any(
            getattr(result, key) != value
            for key, value in command.selection.model_dump().items()
        )
        or not result.enabled
        or result.revision
        not in {command.expected_revision, command.expected_revision + 1}
    ):
        raise HTTPException(502, "员工岗位配置结果与请求不一致")
    return result


async def remove(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
    channel: KnowledgeChannel,
    revision: int,
) -> AgentActivationPolicyRecord:
    response = await client._make_request(
        "DELETE",
        f"/api/v1/agents/{agent}/activation-policies/{channel.value}",
        params={"expected_revision": revision},
        extra_headers=client._trial_headers(project, actor),
    )
    result = decode(response, AgentActivationPolicyRecord)
    if (
        result.channel != channel
        or result.enabled
        or result.revision not in {revision, revision + 1}
    ):
        raise HTTPException(502, "员工岗位配置结果与请求不一致")
    return result
