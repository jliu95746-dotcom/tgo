"""Exact formal-purpose owner resolution, with bounded private responses."""

from datetime import UTC, datetime

import httpx
from pydantic import ValidationError

from app.config import settings
from app.schemas.agent_business_execution import BusinessExecutionCheck
from app.schemas.business_execution_resolution import BusinessExecutionResolution
from app.services.business_connection_client import ConnectionAuthorityUnavailable


class BusinessExecutionOwnerClient:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport

    async def resolve(
        self, check: BusinessExecutionCheck
    ) -> BusinessExecutionResolution:
        token = settings.BUSINESS_CONNECTION_SERVICE_TOKEN
        if token is None or len(token.get_secret_value()) < 32:
            raise ConnectionAuthorityUnavailable()
        command = check.command
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=60,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    "POST",
                    settings.BUSINESS_CONNECTION_API_URL.rstrip("/")
                    + f"/internal/business-connections/{command.connection_id}/business-execution",
                    json=check.private_payload(),
                    headers={"X-Business-Service-Token": token.get_secret_value()},
                ) as response:
                    if response.status_code != 200:
                        raise ConnectionAuthorityUnavailable()
                    data, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 65536:
                            raise ConnectionAuthorityUnavailable()
                        data.append(chunk)
            result = BusinessExecutionResolution.model_validate_json(b"".join(data))
            authority, connection, principal = (
                result.authority,
                result.snapshot.connection,
                result.principal,
            )
            now = datetime.now(UTC)
            if (
                not authority.matches(command)
                or authority.phase != check.phase
                or any(
                    value.model_fields_set != set(type(value).model_fields)
                    for value in (result, authority, principal, connection)
                )
                or authority.expires_at <= now
                or not -5 <= (now - authority.checked_at).total_seconds() <= 30
                or principal.context_digest != command.context_digest
                or principal.expires_at <= now
                or principal.model_dump(
                    include=set(type(command.context).model_fields) - {"context_token"}
                )
                != command.context.model_dump()
                or connection.project_id != command.reference.project_id
                or connection.id != command.connection_id
                or not connection.enabled
                or connection.mode != "http"
                or connection.configuration is None
                or command.operation not in connection.capabilities
                or connection.revision != authority.connection_revision
                or connection.configuration_revision != authority.configuration_revision
                or connection.authorization_revision != authority.authorization_revision
                or connection.resource_revision != authority.resource_revision
            ):
                raise ConnectionAuthorityUnavailable()
            return result
        except (httpx.HTTPError, ValidationError, ValueError):
            raise ConnectionAuthorityUnavailable() from None
