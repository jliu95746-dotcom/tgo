"""HTTP response boundary for the existing pooled WuKongIM client."""

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
import logging
from typing import TypeAlias

from fastapi import HTTPException
import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

JsonObject: TypeAlias = dict[str, JsonValue]
IMResponse: TypeAlias = JsonObject | list[JsonObject]
QueryValue: TypeAlias = str | int | float | bool | None
_json_value: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
logger = logging.getLogger(__name__)


class WuKongIMTransport(ABC):
    """Keep transport failures and untrusted response bodies private."""

    base_url: str
    enabled: bool

    @abstractmethod
    def _get_http_client(self) -> httpx.AsyncClient:
        """Use the existing client's connection pool."""

    @staticmethod
    def _object_response(value: IMResponse) -> JsonObject:
        if not isinstance(value, dict):
            raise HTTPException(502, "WuKongIM returned an invalid object")
        return value

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        json_data: Mapping[str, object] | Sequence[object] | None = None,
        params: Mapping[str, QueryValue] | None = None,
        allow_not_found: bool = False,
        strict_response: bool = False,
    ) -> IMResponse:
        """Preserve request options; reject malformed nonempty responses.

        ``strict_response`` remains accepted for existing lookup callers.
        Invalid nonempty bodies never acknowledge a successful operation.
        """
        if not self.enabled:
            logger.debug("WuKongIM integration is disabled; skipping request")
            return {}
        try:
            response = await self._get_http_client().request(
                method=method,
                url=f"{self.base_url}{endpoint}",
                json=json_data,
                params=params,
            )
        except httpx.TimeoutException:
            logger.error("WuKongIM request timeout")
            raise HTTPException(500, "WuKongIM service timeout") from None
        except httpx.RequestError as exc:
            logger.error(
                "WuKongIM request failed",
                extra={"error_type": type(exc).__name__},
            )
            raise HTTPException(500, "WuKongIM service unavailable") from None

        if response.status_code == 404 and allow_not_found:
            return {}
        if response.status_code != 200:
            logger.error(
                "WuKongIM error response",
                extra={"status_code": response.status_code},
            )
            raise HTTPException(500, "WuKongIM service error") from None
        if not response.text:
            return {}
        try:
            value = _json_value.validate_python(response.json(), strict=True)
        except (ValueError, UnicodeError, ValidationError):
            raise HTTPException(
                502, "WuKongIM returned invalid JSON"
            ) from None
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            items: list[JsonObject] = []
            for item in value:
                if not isinstance(item, dict):
                    raise HTTPException(
                        502, "WuKongIM returned an invalid list item"
                    )
                items.append(item)
            return items
        raise HTTPException(502, "WuKongIM returned an invalid response")
