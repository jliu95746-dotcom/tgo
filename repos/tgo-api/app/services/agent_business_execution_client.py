"""Check exact-purpose AI authority without interpreting any upstream private error."""

from datetime import UTC, datetime
from typing import Protocol

import httpx
from fastapi import HTTPException
from pydantic import JsonValue

from app.schemas.agent_business_execution import (
    BusinessExecutionAuthority,
    BusinessExecutionCheck,
)


class ExecutionTransport(Protocol):
    def _execution_headers(
        self, project_id: str, channel: str | None
    ) -> dict[str, str]: ...
    async def _make_request(
        self,
        method: str,
        endpoint: str,
        *,
        json_data: dict[str, JsonValue],
        extra_headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response: ...


async def authorize(
    client: ExecutionTransport, check: BusinessExecutionCheck
) -> BusinessExecutionAuthority:
    ref = check.command.reference
    response = await client._make_request(
        "POST",
        "/api/v1/agents/business-execution/authorize",
        json_data=check.private_payload(),
        extra_headers=client._execution_headers(
            str(ref.project_id), ref.channel.value if ref.channel else None
        ),
        timeout=30,
    )
    if response.status_code != 200 or len(response.content) > 65536:
        raise HTTPException(409, "当前正式查询授权无法确认")
    try:
        result = BusinessExecutionAuthority.model_validate_json(response.content)
        if (
            not result.matches(check.command)
            or result.phase != check.phase
            or result.model_fields_set != set(BusinessExecutionAuthority.model_fields)
            or not -5 <= (datetime.now(UTC) - result.checked_at).total_seconds() <= 30
            or result.expires_at <= datetime.now(UTC)
        ):
            raise ValueError("Invalid business authority")
    except ValueError:
        raise HTTPException(502, "正式查询授权回执无效") from None
    return result
