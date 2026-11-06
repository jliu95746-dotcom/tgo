"""Typed HTTP, serialization and JSON boundaries for the existing AI client."""

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import NotRequired, TypeAlias, TypedDict
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException
from pydantic import JsonValue, TypeAdapter, ValidationError

from app.core.logging import get_logger

JsonObject: TypeAlias = dict[str, JsonValue]
QueryPrimitive: TypeAlias = str | int | float | bool | None
QueryValue: TypeAlias = QueryPrimitive | Sequence[QueryPrimitive]
RequestParams: TypeAlias = dict[str, QueryValue]
UploadParts: TypeAlias = dict[str, tuple[str, bytes, str]]
_json_value: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
logger = get_logger("ai_client")


def response_objects(value: JsonValue) -> list[JsonObject]:
    """Validate a collection without dropping items or unknown fields."""
    if not isinstance(value, list):
        raise HTTPException(
            502, "AI service returned an invalid list response"
        )
    rows: list[JsonObject] = []
    for row in value:
        if not isinstance(row, dict):
            raise HTTPException(
                502, "AI service returned an invalid list item"
            )
        rows.append(row)
    return rows


class RequestArguments(TypedDict):
    method: str
    url: str
    headers: dict[str, str]
    params: Mapping[str, QueryValue] | None
    json: NotRequired[JsonValue]
    content: NotRequired[str]
    data: NotRequired[dict[str, str] | None]
    files: NotRequired[UploadParts]


class AITransport:
    """Keep one configured transport for JSON, skill files and media."""

    base_url: str
    timeout: int
    api_key: str | None

    def _get_headers(self) -> dict[str, str]:
        """Keep service and reply-permit headers on their original scope."""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "TGO-API-Service/0.1.0",
        }
        # Application permissions and billing share the existing runtime scope.
        from app.services.ai_usage_runtime import authorization_headers

        headers.update(authorization_headers())
        return headers

    def _to_jsonable(self, obj: object) -> JsonValue:
        """Convert values with the existing UUID, date and model support.

        Keep collection conversion and the string fallback for custom objects.
        """
        if obj is None:
            return None
        if isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        if isinstance(obj, dict):
            return {str(k): self._to_jsonable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [self._to_jsonable(v) for v in obj]
        # Support Pydantic models without importing pydantic explicitly
        if hasattr(obj, "model_dump"):
            try:
                return self._to_jsonable(
                    getattr(obj, "model_dump")(exclude_none=True)
                )
            except Exception:
                pass
        try:
            return _json_value.validate_python(
                json.loads(json.dumps(obj, default=str)), strict=True
            )
        except Exception:
            return str(obj)

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        json_data: Mapping[str, object] | None = None,
        params: Mapping[str, QueryValue] | None = None,
        extra_headers: Mapping[str, str] | None = None,
        content: str | None = None,
        form_data: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """Make HTTP request to AI service."""
        url = f"{self.base_url}{endpoint}"
        request_id = str(uuid4())

        headers = self._get_headers()
        headers["X-Request-ID"] = request_id
        if extra_headers:
            headers.update(extra_headers)

        logger.info(
            "AI service request",
            extra={
                "request_id": request_id,
                "method": method,
            },
        )

        try:
            async with httpx.AsyncClient(
                timeout=timeout if timeout is not None else self.timeout,
                trust_env=False,
            ) as client:
                kwargs: RequestArguments = {
                    "method": method,
                    "url": url,
                    "headers": headers,
                    "params": params,
                }
                if json_data is not None:
                    kwargs["json"] = self._to_jsonable(json_data)
                if content is not None:
                    kwargs["content"] = content
                if files is not None:
                    if content is not None or json_data is not None:
                        raise ValueError(
                            "Multipart and JSON content cannot be combined"
                        )
                    headers.pop("Content-Type", None)
                    kwargs["data"] = form_data
                    kwargs["files"] = files
                response = await client.request(**kwargs)

                logger.info(
                    f"AI service response: {response.status_code}",
                    extra={
                        "request_id": request_id,
                        "status_code": response.status_code,
                        "response_time": response.elapsed.total_seconds()
                        if response.elapsed
                        else None,
                    },
                )

                return response

        except httpx.TimeoutException:
            logger.error(
                "AI service timeout",
                extra={
                    "request_id": request_id,
                    "timeout": timeout
                    if timeout is not None
                    else self.timeout,
                },
            )
            raise HTTPException(
                status_code=504, detail="AI service request timed out"
            )
        except httpx.RequestError as e:
            logger.error(
                "AI service request failed",
                extra={
                    "request_id": request_id,
                    "error_type": type(e).__name__,
                },
            )
            raise HTTPException(
                status_code=502, detail="Failed to connect to AI service"
            )

    async def _handle_response(self, response: httpx.Response) -> JsonValue:
        """Handle AI service response and convert errors."""
        if response.is_success:
            if response.status_code == 204:
                return None
            try:
                return _json_value.validate_python(
                    response.json(), strict=True
                )
            except (ValueError, UnicodeError, ValidationError):
                return response.text

        error_data = self._error_detail(response)

        logger.warning(
            f"AI service error response: {response.status_code}",
            extra={
                "status_code": response.status_code,
            },
        )

        raise HTTPException(
            status_code=response.status_code, detail=error_data
        )

    @staticmethod
    def _error_detail(response: httpx.Response) -> JsonValue:
        try:
            return _json_value.validate_python(response.json(), strict=True)
        except (ValueError, UnicodeError, ValidationError):
            return {"error": {"message": "Invalid AI service response"}}

    @staticmethod
    def _event_data(text: str) -> JsonValue:
        try:
            return _json_value.validate_python(json.loads(text), strict=True)
        except (ValueError, ValidationError):
            return text

    async def _object_response(self, response: httpx.Response) -> JsonObject:
        result = await self._handle_response(response)
        if not isinstance(result, dict):
            raise HTTPException(502, "AI service returned an invalid response")
        return result

    async def _object_list_response(
        self, response: httpx.Response
    ) -> list[JsonObject]:
        return response_objects(await self._handle_response(response))
