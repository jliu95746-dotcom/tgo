"Observe the registered private execution protocol without granting \
permission."

from datetime import UTC, datetime

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.schemas.agent_business_execution import (
    BusinessRuntimeCapabilities,
    BusinessRuntimeQuery,
    BusinessRuntimeReadiness,
)
from app.schemas.agent_configuration_actor import AgentConfigurationActorQuery
from app.services.agent_configuration_actor import current_configuration_actor


async def current_business_runtime(
    db: Session,
    query: BusinessRuntimeQuery,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> BusinessRuntimeReadiness:
    actor = AgentConfigurationActorQuery(**query.model_dump())
    current_configuration_actor(db, actor)
    available = False
    token = settings.BUSINESS_CONNECTION_SERVICE_TOKEN
    if token is not None and len(token.get_secret_value()) >= 32:
        try:
            async with httpx.AsyncClient(
                timeout=2,
                transport=transport,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "GET",
                    settings.PLUGIN_RUNTIME_URL.rstrip("/")
                    + "/business/execution/capabilities",
                    headers={
                        "X-Business-Service-Token": token.get_secret_value()
                    },
                ) as response:
                    if response.status_code != 200:
                        raise ValueError("Runtime protocol unavailable")
                    data, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 4096:
                            raise ValueError("Runtime response too large")
                        data.append(chunk)
            capabilities = BusinessRuntimeCapabilities.model_validate_json(
                b"".join(data)
            )
            available = (
                capabilities.model_fields_set
                == set(BusinessRuntimeCapabilities.model_fields)
                and -5
                <= (
                    datetime.now(UTC) - capabilities.checked_at
                ).total_seconds()
                <= 30
            )
        except (httpx.HTTPError, ValueError):
            available = False
    current_configuration_actor(db, actor)
    return BusinessRuntimeReadiness(
        **query.model_dump(),
        available=available,
        operations=[
            "order_query",
            "logistics_query",
            "customer_profile_query",
            "membership_query",
        ]
        if available
        else [],
        checked_at=datetime.now(UTC),
    )
