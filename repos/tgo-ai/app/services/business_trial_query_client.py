"""AI never receives connection credentials; only a safe identified receipt."""

from datetime import UTC, datetime, timedelta

import httpx
from pydantic import ValidationError

from app.config import settings
from app.exceptions import ExternalServiceError
from app.schemas.agent_business_trials import BusinessTrialCommand, BusinessTrialReceipt


async def query(
    url: str,
    command: BusinessTrialCommand,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> BusinessTrialReceipt:
    started = datetime.now(UTC)
    try:
        async with httpx.AsyncClient(
            timeout=100, transport=transport, trust_env=False, follow_redirects=False
        ) as client:
            async with client.stream(
                "POST",
                url.rstrip("/") + "/ai/business-trials/query",
                json=command.private_payload(),
                headers={"X-Internal-API-Key": settings.secret_key},
            ) as response:
                if response.status_code != 200:
                    raise ValueError("Unconfirmed business trial")
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 65_536:
                        raise ValueError("Unconfirmed business trial")
                    chunks.append(chunk)
        result = BusinessTrialReceipt.model_validate_json(b"".join(chunks))
        if (
            not result.matches(command)
            or result.model_fields_set != set(BusinessTrialReceipt.model_fields)
            or result.customer_evidence.model_fields_set
            != set(type(result.customer_evidence).model_fields)
            or not started - timedelta(seconds=5)
            <= result.checked_at
            <= datetime.now(UTC) + timedelta(seconds=5)
        ):
            raise ValueError("Unconfirmed business trial")
        return result
    except (httpx.HTTPError, ValidationError, ValueError):
        raise ExternalServiceError(
            "tgo-api", "业务测试结果暂不可确认，请查询原请求"
        ) from None
