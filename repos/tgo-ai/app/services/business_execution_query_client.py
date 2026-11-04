"""Formal query transport has no retries, raw-input logging or test-purpose fallback."""

from datetime import UTC, datetime

import httpx
from pydantic import ValidationError

from app.config import settings
from app.exceptions import AuthorizationError, ExternalServiceError
from app.schemas.agent_business_execution import (
    BusinessExecutionCommand,
    BusinessExecutionReceipt,
)


async def query(
    client: httpx.AsyncClient, internal_url: str, command: BusinessExecutionCommand
) -> BusinessExecutionReceipt:
    try:
        async with client.stream(
            "POST",
            internal_url.rstrip("/") + "/ai/business-execution/query",
            json=command.private_payload(),
            headers={"X-Internal-API-Key": settings.secret_key},
            timeout=90,
        ) as response:
            if response.status_code in {401, 403, 409}:
                raise AuthorizationError("当前业务查询授权已失效")
            if response.status_code != 200:
                raise ExternalServiceError("tgo-api", "业务结果暂不可核验")
            data, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > 65536:
                    raise ValueError("Oversized business receipt")
                data.append(chunk)
        result = BusinessExecutionReceipt.model_validate_json(b"".join(data))
        if (
            not result.matches(command)
            or not result.authority.matches(command)
            or not set(BusinessExecutionReceipt.model_fields).issubset(
                result.model_fields_set
            )
            or result.authority.expires_at <= datetime.now(UTC)
            or result.authority.model_fields_set != set(type(result.authority).model_fields)
            or not -5 <= (datetime.now(UTC) - result.checked_at).total_seconds() <= 30
        ):
            raise ValueError("Wrong business receipt")
        return result
    except (httpx.HTTPError, ValidationError, ValueError):
        raise ExternalServiceError("tgo-api", "业务结果暂不可核验") from None
