"""The adapter obtains no credentials from its public caller or global defaults."""

import httpx
from pydantic import ValidationError

from app.config import settings
from app.schemas.business_connections import BusinessConnectionPrivateSnapshot
from app.schemas.connection_test_protocol import ConnectionTestCommand


class ConnectionAuthorityUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("业务连接的原配置或当前权限无法确认")


class BusinessConnectionOwnerClient:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport

    async def resolve(
        self, command: ConnectionTestCommand
    ) -> BusinessConnectionPrivateSnapshot:
        key = settings.BUSINESS_CONNECTION_SERVICE_TOKEN
        if key is None or len(key.get_secret_value()) < 32:
            raise ConnectionAuthorityUnavailable()
        url = (
            settings.BUSINESS_CONNECTION_API_URL.rstrip("/")
            + f"/internal/business-connections/{command.connection_id}/resolve"
        )
        body = {
            "project_id": str(command.project_id),
            "actor_id": str(command.actor_id),
            "expected_revision": command.expected_revision,
            "purpose": "configuration_test",
        }
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=10,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    url,
                    json=body,
                    headers={"X-Business-Service-Token": key.get_secret_value()},
                )
            if response.status_code != 200 or len(response.content) > 65_536:
                raise ConnectionAuthorityUnavailable()
            value = BusinessConnectionPrivateSnapshot.model_validate_json(
                response.content
            )
            connection = value.connection
            if (
                value.model_fields_set
                != {"scope", "can_enable", "connection", "credentials"}
                or value.can_enable is not False
                or connection.project_id != command.project_id
                or connection.id != command.connection_id
                or connection.revision != command.expected_revision
                or connection.configuration_revision != command.configuration_revision
                or connection.authorization_revision != command.authorization_revision
                or connection.resource_revision != command.resource_revision
                or command.operation not in connection.capabilities
            ):
                raise ConnectionAuthorityUnavailable()
            return value
        except (httpx.HTTPError, ValidationError, ValueError):
            raise ConnectionAuthorityUnavailable() from None
