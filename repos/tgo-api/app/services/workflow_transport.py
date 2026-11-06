"""Typed transport for the existing internal Workflow service client."""

import json
from collections.abc import Mapping, Sequence
from typing import TypeAlias
from uuid import uuid4

import httpx
from fastapi import HTTPException
from pydantic import JsonValue, TypeAdapter, ValidationError

from app.core.logging import get_logger

JsonObject: TypeAlias = dict[str, JsonValue]
QueryPrimitive: TypeAlias = str | int | float | bool | None
QueryValue: TypeAlias = QueryPrimitive | Sequence[QueryPrimitive]
RequestParams: TypeAlias = dict[str, QueryValue]
_json_value: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
logger = get_logger("workflow_client")


class WorkflowTransport:
    """Keep request settings and JSON checks at the HTTP boundary."""

    base_url: str
    timeout: int
    api_key: str | None

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "TGO-API-Service/0.1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        json_data: Mapping[str, object] | None = None,
        params: Mapping[str, QueryValue] | None = None,
        extra_headers: Mapping[str, str] | None = None,
        raw_body: bytes | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        if raw_body is not None and json_data is not None:
            raise ValueError(
                "Workflow request cannot contain both raw and JSON bodies"
            )
        request_id = str(uuid4())
        headers = self._get_headers()
        headers["X-Request-ID"] = request_id
        if extra_headers:
            headers.update(extra_headers)
        logger.info(
            "Workflow service request",
            extra={"request_id": request_id, "method": method},
        )
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout if timeout is None else timeout,
                trust_env=False,
            ) as client:
                response = await client.request(
                    method=method,
                    url=f"{self.base_url}{endpoint}",
                    headers=headers,
                    json=json_data,
                    content=raw_body,
                    params=params,
                )
                logger.info(
                    "Workflow service response",
                    extra={
                        "request_id": request_id,
                        "status_code": response.status_code,
                        "response_time": response.elapsed.total_seconds(),
                    },
                )
                return response
        except httpx.TimeoutException:
            logger.error(
                "Workflow service timeout", extra={"request_id": request_id}
            )
            raise HTTPException(
                status_code=504, detail="Workflow service request timed out"
            ) from None
        except httpx.RequestError as exc:
            logger.error(
                "Workflow service request failed",
                extra={
                    "request_id": request_id,
                    "error_type": type(exc).__name__,
                },
            )
            raise HTTPException(
                status_code=502, detail="Failed to connect to Workflow service"
            ) from None

    @staticmethod
    def _error_detail(response: httpx.Response) -> JsonValue:
        try:
            return _json_value.validate_python(response.json(), strict=True)
        except (ValueError, UnicodeError, ValidationError):
            return {"error": {"message": "Invalid Workflow service response"}}

    async def _handle_response(self, response: httpx.Response) -> JsonValue:
        if response.is_success and response.status_code == 204:
            return None
        if not response.is_success:
            raise HTTPException(
                status_code=response.status_code,
                detail=self._error_detail(response),
            )
        try:
            return _json_value.validate_python(response.json(), strict=True)
        except (json.JSONDecodeError, UnicodeError, ValidationError):
            raise HTTPException(
                status_code=502,
                detail="Workflow service returned an invalid JSON response",
            ) from None

    async def _object_response(self, response: httpx.Response) -> JsonObject:
        result = await self._handle_response(response)
        if not isinstance(result, dict):
            raise HTTPException(
                status_code=502,
                detail="Workflow service returned an invalid response",
            )
        return result

    async def _object_list_response(
        self, response: httpx.Response
    ) -> list[JsonObject]:
        result = await self._handle_response(response)
        if not isinstance(result, list):
            raise HTTPException(
                status_code=502,
                detail="Workflow service returned an invalid list response",
            )
        items: list[JsonObject] = []
        for item in result:
            if not isinstance(item, dict):
                raise HTTPException(
                    status_code=502,
                    detail="Workflow service returned an invalid list item",
                )
            items.append(item)
        return items
