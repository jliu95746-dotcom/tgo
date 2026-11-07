"""Client for forwarding supervisor agent run requests to the agent service."""

from __future__ import annotations

import logging
from typing import AsyncIterator, Dict, Optional

import httpx
from pydantic import JsonValue, TypeAdapter

from app.config import settings
from app.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ExternalServiceError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from app.schemas.agent_run import SupervisorRunRequest


logger = logging.getLogger(__name__)
_json_object = TypeAdapter(dict[str, JsonValue])


class AgentRuntimeServiceClient:
    """HTTP client wrapper around the external agent runtime service."""

    def __init__(self) -> None:
        self.base_url = settings.agent_service_url.rstrip("/")
        # Use generous timeouts for long-running agent executions
        self.request_timeout = httpx.Timeout(
            120.0, connect=10.0, write=30.0, read=120.0,
        )
        self.stream_timeout = httpx.Timeout(
            None, connect=10.0, write=30.0, read=None,
        )

    def _build_headers(
        self,
        api_key: str,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": api_key,
            "User-Agent": "tgo-ai-service",
        }
        if extra_headers:
            headers.update(extra_headers)
        return headers

    async def run_supervisor(
        self,
        payload: SupervisorRunRequest,
        api_key: str,
        *,
        request_id: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> dict[str, JsonValue]:
        """
        Execute a non-streaming supervisor run request.

        Returns the parsed JSON response when successful and raises
        service-specific exceptions for failure scenarios.
        """
        json_payload = payload.model_dump(mode="json", exclude_none=True)
        headers = self._build_headers(api_key, extra_headers)
        if request_id:
            headers.setdefault("X-Request-ID", request_id)

        url = f"{self.base_url}/run"
        logger.info(
            "Forwarding non-streaming supervisor run request",
            extra={"request_id": request_id},
        )

        try:
            async with httpx.AsyncClient(
                timeout=self.request_timeout,
            ) as client:
                response = await client.post(
                    url, json=json_payload, headers=headers,
                )
        except httpx.TimeoutException:
            logger.error("Agent service request timed out")
            raise ExternalServiceError(
                "agent", message="Agent service request timed out",
            ) from None
        except httpx.RequestError:
            logger.error("Failed to connect to agent service")
            raise ExternalServiceError(
                "agent", message="Unable to reach agent service",
            ) from None

        if response.status_code >= 400:
            await self._raise_for_status(response)

        try:
            return _json_object.validate_python(response.json(), strict=True)
        except (TypeError, ValueError):
            raise ExternalServiceError(
                "agent", message="Invalid response from agent service",
                details={"status_code": response.status_code},
            ) from None

    async def stream_supervisor(
        self,
        payload: SupervisorRunRequest,
        api_key: str,
        *,
        request_id: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> AsyncIterator[str]:
        """Stream supervisor execution events from the agent service."""
        json_payload = payload.model_dump(mode="json", exclude_none=True)
        json_payload["stream"] = True

        headers = self._build_headers(api_key, extra_headers)
        if request_id:
            headers.setdefault("X-Request-ID", request_id)

        url = f"{self.base_url}/run"
        logger.info(
            "Forwarding streaming supervisor run request",
            extra={"request_id": request_id},
        )

        async def event_stream() -> AsyncIterator[str]:
            try:
                async with httpx.AsyncClient(
                    timeout=self.stream_timeout,
                ) as client:
                    async with client.stream(
                        "POST", url, json=json_payload, headers=headers,
                    ) as response:
                        if response.status_code >= 400:
                            await self._raise_for_status(response)

                        async for chunk in response.aiter_text():
                            if chunk:
                                yield chunk
            except httpx.TimeoutException:
                logger.error("Streaming agent service request timed out")
                raise ExternalServiceError(
                    "agent", message="Agent service stream timed out",
                ) from None
            except httpx.RequestError:
                logger.error("Failed to stream from agent service")
                raise ExternalServiceError(
                    "agent", message="Unable to stream from agent service",
                ) from None

        return event_stream()

    async def _raise_for_status(self, response: httpx.Response) -> None:
        """Translate HTTP error responses into domain-specific exceptions."""
        status_code = response.status_code

        logger.warning(
            "Agent service returned error",
            extra={
                "status_code": status_code,
            },
        )

        if status_code in (400, 422):
            raise ValidationError(
                "Invalid request to agent service",
                details={"status_code": status_code},
            )
        if status_code == 401:
            raise AuthenticationError(details={"service": "agent"})
        if status_code == 403:
            raise AuthorizationError(details={"service": "agent"})
        if status_code == 404:
            raise NotFoundError("AgentRun", None, {"status_code": status_code})
        if status_code == 429:
            raise RateLimitError(details={"service": "agent"})

        raise ExternalServiceError(
            "agent",
            message="Agent service returned an unexpected error",
            details={"status_code": status_code},
        )


# Shared client instance
agent_runtime_service_client = AgentRuntimeServiceClient()
