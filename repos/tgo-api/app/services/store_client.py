"""Store HTTP boundary; untrusted responses never enter failure messages."""

from collections.abc import Mapping
from typing import TypeAlias

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError
from typing_extensions import TypedDict

from app.core.config import settings
from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger
from app.schemas.store import StoreModelDetail

logger = get_logger("store_client")
JsonObject: TypeAlias = dict[str, JsonValue]


class StoreAPIKeyData(TypedDict):
    """Required credential fields, validated before local persistence."""

    user_id: str
    email: str
    api_key: str


_json_object: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_api_key_data: TypeAdapter[StoreAPIKeyData] = TypeAdapter(StoreAPIKeyData)


class StoreClient:
    """Client for interacting with the Store API (Tools and Models)."""

    def __init__(self) -> None:
        self.base_url = f"{settings.STORE_SERVICE_URL.rstrip('/')}/api/v1"
        self.timeout = settings.STORE_TIMEOUT

    async def _request(
        self, method: str, path: str, headers: Mapping[str, str]
    ) -> JsonObject:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.request(
                    method, f"{self.base_url}{path}", headers=headers
                )
                response.raise_for_status()
                try:
                    return _json_object.validate_python(
                        response.json(), strict=True
                    )
                except (ValueError, UnicodeError, ValidationError):
                    raise ExternalServiceError(
                        "store", "Store returned an invalid response"
                    ) from None
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "Store API HTTP error",
                    extra={"status_code": exc.response.status_code},
                )
                raise
            except Exception as exc:
                logger.error(
                    "Store request failed",
                    extra={"error_type": type(exc).__name__},
                )
                raise

    async def get_model(self, model_id: str, api_key: str) -> StoreModelDetail:
        """Fetch model details from Store."""
        data = await self._request(
            "GET", f"/models/{model_id}", {"X-API-Key": api_key}
        )
        try:
            model = StoreModelDetail.model_validate(data)
        except ValidationError:
            raise ExternalServiceError(
                "store", "Store returned invalid model details"
            ) from None
        if not all(
            (
                model.id,
                model.name,
                model.model_type,
                model.provider.id,
                model.provider.name,
                model.provider.slug,
            )
        ):
            raise ExternalServiceError(
                "store", "Store returned invalid model details"
            )
        return model

    async def install_model(self, model_id: str, api_key: str) -> JsonObject:
        """Mark model as installed in Store."""
        return await self._request(
            "POST", f"/install/model/{model_id}", {"X-API-Key": api_key}
        )

    async def uninstall_model(self, model_id: str, api_key: str) -> JsonObject:
        """Mark model as uninstalled in Store."""
        return await self._request(
            "DELETE", f"/install/model/{model_id}", {"X-API-Key": api_key}
        )

    async def get_api_key(self, access_token: str) -> StoreAPIKeyData:
        """Fetch complete credentials from Store using its access token."""
        data = await self._request(
            "GET",
            "/auth/api-key",
            {"Authorization": f"Bearer {access_token}"},
        )
        try:
            credential = _api_key_data.validate_python(data, strict=True)
        except ValidationError:
            raise ExternalServiceError(
                "store", "Store returned invalid credentials"
            ) from None
        if not all(
            (
                credential["user_id"].strip(),
                credential["email"].strip(),
                credential["api_key"].strip(),
            )
        ):
            raise ExternalServiceError(
                "store", "Store returned invalid credentials"
            )
        return credential


store_client = StoreClient()
