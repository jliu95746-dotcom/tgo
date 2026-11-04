"""Bounded typed transport for the adapter's private configuration test API."""

from datetime import UTC, datetime, timedelta

import httpx
from pydantic import ValidationError

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.schemas.connection_test_protocol import (
    ConnectionTestCommand,
    ConnectionTestReceipt,
)


def unavailable() -> TGOAPIException:
    return TGOAPIException(
        "业务连接测试暂不可用，请查询原请求后重试",
        code="CONNECTION_TEST_UNAVAILABLE",
        status_code=503,
    )


async def request_connection_test(
    base_url: str,
    timeout: float,
    command: ConnectionTestCommand,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ConnectionTestReceipt:
    token = settings.BUSINESS_CONNECTION_SERVICE_TOKEN
    if token is None or len(token.get_secret_value()) < 32:
        raise unavailable()
    started = datetime.now(UTC)
    try:
        async with httpx.AsyncClient(
            timeout=min(max(timeout, 60), 90),
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            async with client.stream(
                "POST",
                base_url.rstrip("/") + "/business/connections/test",
                json=command.model_dump(mode="json"),
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
        result = ConnectionTestReceipt.model_validate_json(b"".join(chunks))
        if (
            not result.matches(command)
            or result.model_fields_set != set(type(result).model_fields)
            or result.checked_at < started - timedelta(seconds=5)
            or result.checked_at > datetime.now(UTC) + timedelta(seconds=5)
        ):
            raise unavailable()
        return result
    except (httpx.HTTPError, ValidationError, ValueError):
        raise unavailable() from None
