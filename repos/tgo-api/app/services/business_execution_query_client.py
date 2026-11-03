"""Bounded formal Plugin transport, never the legacy global customer query."""

from datetime import UTC, datetime

import httpx
from pydantic import ValidationError

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.schemas.agent_business_execution import (
    BusinessExecutionCommand,
    BusinessExecutionReceipt,
)


def unavailable() -> TGOAPIException:
    return TGOAPIException(
        "业务查询结果暂不可确认，请稍后重试或转人工",
        code="BUSINESS_EXECUTION_UNAVAILABLE",
        status_code=503,
    )


async def query(
    command: BusinessExecutionCommand,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> BusinessExecutionReceipt:
    token = settings.BUSINESS_CONNECTION_SERVICE_TOKEN
    if token is None or len(token.get_secret_value()) < 32:
        raise unavailable()
    try:
        async with httpx.AsyncClient(
            timeout=90, transport=transport, trust_env=False, follow_redirects=False
        ) as client:
            async with client.stream(
                "POST",
                settings.PLUGIN_RUNTIME_URL.rstrip("/") + "/business/execution/query",
                json=command.private_payload(),
                headers={"X-Business-Service-Token": token.get_secret_value()},
            ) as response:
                if response.status_code != 200:
                    raise unavailable()
                data, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 65536:
                        raise unavailable()
                    data.append(chunk)
        result = BusinessExecutionReceipt.model_validate_json(b"".join(data))
        if (
            not result.matches(command)
            or result.authority.expires_at <= datetime.now(UTC)
            or result.model_fields_set != set(BusinessExecutionReceipt.model_fields)
            or result.authority.model_fields_set != set(type(result.authority).model_fields)
            or not -5 <= (datetime.now(UTC) - result.checked_at).total_seconds() <= 30
        ):
            raise unavailable()
        return result
    except (httpx.HTTPError, ValidationError, ValueError):
        raise unavailable() from None
