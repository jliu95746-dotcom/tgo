"""Typed HTTP and response boundaries for the existing RAG client."""

from collections.abc import Mapping, Sequence
import json
from typing import BinaryIO, TypeAlias
from uuid import uuid4

from fastapi import HTTPException
import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from app.core.config import settings
from app.core.logging import get_logger

JsonObject: TypeAlias = dict[str, JsonValue]
QueryValue: TypeAlias = str | int | float | bool | None
RequestParams: TypeAlias = dict[str, QueryValue]
FilePart: TypeAlias = tuple[str | None, bytes | BinaryIO, str | None]
UploadParts: TypeAlias = (
    Mapping[str, FilePart] | Sequence[tuple[str, FilePart]]
)
_json_value: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
logger = get_logger("rag_client")


class RAGTransport:
    """Keep JSON, multipart and downloads on the same configured transport."""

    def __init__(self) -> None:
        self.base_url = settings.RAG_SERVICE_URL.rstrip("/")
        self.timeout = settings.RAG_SERVICE_TIMEOUT
        self.api_key = settings.RAG_SERVICE_API_KEY

    def _get_headers(self) -> dict[str, str]:
        headers = self._get_multipart_headers()
        headers["Content-Type"] = "application/json"
        return headers

    def _get_multipart_headers(self) -> dict[str, str]:
        headers = {"User-Agent": "TGO-API-Service/0.1.0"}
        if (
            settings.SAAS_ENABLED
            and settings.SAAS_BILLING_ENABLED
            and settings.SAAS_INTERNAL_TOKEN
        ):
            headers[
                "X-SaaS-Service-Token"
            ] = settings.SAAS_INTERNAL_TOKEN.get_secret_value()
        return headers

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        json_data: Mapping[str, object] | None = None,
        params: Mapping[str, QueryValue] | None = None,
        files: UploadParts | None = None,
        data: Mapping[str, str] | None = None,
        *,
        _download: bool = False,
    ) -> httpx.Response:
        request_id = str(uuid4())
        headers = (
            self._get_multipart_headers() if files else self._get_headers()
        )
        headers["X-Request-ID"] = request_id
        logger.info(
            "RAG service request",
            extra={
                "request_id": request_id,
                "method": method,
                "has_files": bool(files),
                "download": _download,
            },
        )
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method=method,
                    url=f"{self.base_url}{endpoint}",
                    headers=headers,
                    json=json_data,
                    params=params,
                    files=files,
                    data=data,
                )
                logger.info(
                    "RAG service response",
                    extra={
                        "request_id": request_id,
                        "status_code": response.status_code,
                        "response_time": response.elapsed.total_seconds(),
                    },
                )
                return response
        except httpx.TimeoutException:
            logger.error(
                "RAG service timeout",
                extra={"request_id": request_id, "timeout": self.timeout},
            )
            raise HTTPException(
                status_code=504,
                detail=(
                    "RAG service download request timed out"
                    if _download
                    else "RAG service request timed out"
                ),
            ) from None
        except httpx.RequestError as exc:
            logger.error(
                "RAG service request failed",
                extra={
                    "request_id": request_id,
                    "error_type": type(exc).__name__,
                },
            )
            raise HTTPException(
                status_code=502,
                detail=(
                    "Failed to connect to RAG service for file download"
                    if _download
                    else "Failed to connect to RAG service"
                ),
            ) from None

    async def _handle_response(self, response: httpx.Response) -> JsonValue:
        if response.is_success and response.status_code == 204:
            return None
        try:
            result = _json_value.validate_python(response.json(), strict=True)
        except (json.JSONDecodeError, UnicodeError, ValidationError):
            if response.is_success:
                return response.text
            result = {
                "error": {"message": response.text or "Unknown error"},
            }
        if response.is_success:
            return result
        logger.warning(
            "RAG service error response",
            extra={"status_code": response.status_code},
        )
        raise HTTPException(status_code=response.status_code, detail=result)

    async def _object_response(self, response: httpx.Response) -> JsonObject:
        result = await self._handle_response(response)
        if not isinstance(result, dict):
            raise HTTPException(
                status_code=502,
                detail="RAG service returned an invalid response",
            )
        return result

    async def _paginated_response(
        self, response: httpx.Response
    ) -> JsonObject:
        result = await self._object_response(response)
        if "pagination" in result:
            pagination = result["pagination"]
            if not isinstance(pagination, dict):
                raise HTTPException(
                    status_code=502,
                    detail="RAG service returned invalid pagination",
                )
            if "has_previous" in pagination:
                pagination["has_prev"] = pagination.pop("has_previous")
        return result

    async def download_file(
        self,
        project_id: str,
        file_id: str,
    ) -> httpx.Response:
        """Return the unmodified buffered response, as the prior client did."""
        return await self._make_request(
            "GET",
            f"/v1/files/{file_id}/download",
            params={"project_id": project_id},
            _download=True,
        )
