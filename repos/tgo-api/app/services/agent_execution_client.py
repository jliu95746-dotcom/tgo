"""Recheck an exact private AI execution immediately before publication."""
from datetime import UTC, datetime
from typing import Protocol

from fastapi import HTTPException
from pydantic import JsonValue, ValidationError
import httpx

from app.schemas.agent_execution import (
    AgentExecutionCheckReceipt,
    AgentExecutionReference,
)


class ExecutionTransport(Protocol):
    def _execution_headers(
        self, project_id: str, channel: str | None
    ) -> dict[str, str]:
        ...

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        *,
        json_data: dict[str, JsonValue] | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        ...


async def verify(
    client: ExecutionTransport, reference: AgentExecutionReference
) -> AgentExecutionCheckReceipt:
    response = await client._make_request(
        "POST",
        "/api/v1/agents/execution-check",
        json_data=reference.model_dump(mode="json"),
        extra_headers=client._execution_headers(
            str(reference.project_id),
            reference.channel.value if reference.channel else None,
        ),
        timeout=25,
    )
    if not response.is_success:
        raise HTTPException(
            response.status_code, "当前执行授权无法确认，答复暂停发送，请重新核验或转人工"
        )
    try:
        receipt = AgentExecutionCheckReceipt.model_validate_json(
            response.content
        )
    except ValidationError:
        raise HTTPException(502, "执行核验回执无效，答复暂停发送") from None
    if (
        receipt.reference != reference
        or not set(AgentExecutionCheckReceipt.model_fields).issubset(
            receipt.model_fields_set
        )
        or not -30
        <= (datetime.now(UTC) - receipt.checked_at).total_seconds()
        <= 60
    ):
        raise HTTPException(502, "执行核验回执与原请求不一致或已失效")
    return receipt
