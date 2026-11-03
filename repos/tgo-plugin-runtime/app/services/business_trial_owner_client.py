"""Only the API owner releases exact-purpose customer identity and credentials."""

from datetime import UTC, datetime

import httpx
from pydantic import ValidationError

from app.config import settings
from app.schemas.agent_business_trials import BusinessTrialCheck
from app.schemas.business_trial_resolution import BusinessTrialResolution
from app.services.business_connection_client import ConnectionAuthorityUnavailable


class BusinessTrialOwnerClient:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport

    async def resolve(self, check: BusinessTrialCheck) -> BusinessTrialResolution:
        key = settings.BUSINESS_CONNECTION_SERVICE_TOKEN
        if key is None or len(key.get_secret_value()) < 32:
            raise ConnectionAuthorityUnavailable()
        command = check.command
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=25,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    "POST",
                    settings.BUSINESS_CONNECTION_API_URL.rstrip("/")
                    + f"/internal/business-connections/{command.connection_id}/business-trial",
                    json=check.private_payload(),
                    headers={"X-Business-Service-Token": key.get_secret_value()},
                ) as response:
                    if response.status_code != 200:
                        raise ConnectionAuthorityUnavailable()
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 65_536:
                            raise ConnectionAuthorityUnavailable()
                        chunks.append(chunk)
            result = BusinessTrialResolution.model_validate_json(b"".join(chunks))
            authority, connection = result.authority, result.snapshot.connection
            now = datetime.now(UTC)
            if (
                any(
                    value.model_fields_set != set(type(value).model_fields)
                    for value in (
                        result,
                        authority,
                        result.snapshot,
                        connection,
                        result.customer_evidence,
                    )
                )
                or not authority.matches(command)
                or authority.phase != check.phase
                or not -5 <= (now - authority.checked_at).total_seconds() <= 30
                or authority.expires_at <= now
                or result.customer_evidence.expires_at.tzinfo is None
                or result.customer_evidence.expires_at <= now
                or not connection.enabled
                or connection.mode != "http"
                or connection.configuration is None
                or connection.project_id != command.project_id
                or connection.id != command.connection_id
                or connection.revision != command.expected_revision
                or connection.configuration_revision != command.configuration_revision
                or connection.authorization_revision != command.authorization_revision
                or connection.resource_revision != command.resource_revision
                or command.operation not in connection.capabilities
            ):
                raise ConnectionAuthorityUnavailable()
            return result
        except (httpx.HTTPError, ValidationError, ValueError):
            raise ConnectionAuthorityUnavailable() from None
