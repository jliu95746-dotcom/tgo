"""Bounded purpose-specific transport; never uses raw-logging plugin calls."""

from datetime import UTC, datetime, timedelta

import httpx
from pydantic import ValidationError

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.schemas.agent_business_trials import BusinessTrialCommand, BusinessTrialReceipt


def unavailable() -> TGOAPIException:
    return TGOAPIException(
        "业务测试结果暂不可确认，请查询原请求",
        code="BUSINESS_TRIAL_UNAVAILABLE",
        status_code=503,
    )


async def query(
    command: BusinessTrialCommand, *, transport: httpx.AsyncBaseTransport | None = None
) -> BusinessTrialReceipt:
    token = settings.BUSINESS_CONNECTION_SERVICE_TOKEN
    if token is None or len(token.get_secret_value()) < 32:
        raise unavailable()
    started = datetime.now(UTC)
    try:
        async with httpx.AsyncClient(
            timeout=90, transport=transport, trust_env=False, follow_redirects=False
        ) as client:
            async with client.stream(
                "POST",
                settings.PLUGIN_RUNTIME_URL.rstrip("/") + "/business/trials/query",
                json=command.private_payload(),
                headers={"X-Business-Service-Token": token.get_secret_value()},
            ) as response:
                if response.status_code != 200:
                    raise unavailable()
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 65_536:
                        raise unavailable()
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
            raise unavailable()
        return result
    except (httpx.HTTPError, ValidationError, ValueError):
        raise unavailable() from None
