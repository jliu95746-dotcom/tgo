"""
MCP Service Client

HTTP client for tools and other Model Context Protocol service operations.
"""

import logging
from typing import Any, Dict, Optional
import httpx
from pydantic import JsonValue, TypeAdapter

from app.config import settings

logger = logging.getLogger(__name__)
_json_object = TypeAdapter(dict[str, JsonValue])


class MCPServiceError(Exception):
    """Exception raised when MCP service operations fail."""

    def __init__(
        self, message: str, status_code: Optional[int] = None,
        response_data: Optional[dict[str, JsonValue]] = None,
    ) -> None:
        self.message = message
        self.status_code = status_code
        self.response_data = response_data
        super().__init__(message)


class MCPServiceClient:
    """Client for interacting with the MCP service."""

    def __init__(self) -> None:
        self.base_url = settings.mcp_service_url.rstrip('/')
        self.timeout = 30.0

    def _get_headers(
        self, additional_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """Get headers for MCP service requests (no authentication)."""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "TGO-AI-Service/1.0"
        }

        # Add any additional headers
        if additional_headers:
            headers.update(additional_headers)

        return headers

    async def _make_request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None
    ) -> dict[str, JsonValue]:
        """
        Make a request to the MCP service.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            path: API path (e.g., "/v1/tools")
            params: Optional query parameters
            json_data: Optional JSON data for request body
            headers: Optional additional headers

        Returns:
            The JSON object returned by the MCP service.

        Raises:
            MCPServiceError: If the request fails
        """
        url = f"{self.base_url}{path}"
        request_headers = self._get_headers(headers)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                logger.info("Calling MCP service")

                response = await client.request(
                    method=method,
                    url=url,
                    headers=request_headers,
                    params=params,
                    json=json_data
                )

                logger.info(
                    "MCP service responded with status: %s",
                    response.status_code,
                )

                # Handle different response status codes
                if response.status_code in (200, 201):
                    try:
                        return _json_object.validate_python(
                            response.json(), strict=True,
                        )
                    except (TypeError, ValueError):
                        raise MCPServiceError(
                            "Invalid response from MCP service",
                            status_code=502,
                        ) from None
                elif response.status_code == 204:
                    return {}
                elif response.status_code == 404:
                    raise MCPServiceError(
                        "Resource not found in MCP service",
                        status_code=404,
                        response_data={"status_code": 404},
                    )
                elif response.status_code == 400:
                    raise MCPServiceError(
                        "Bad request to MCP service",
                        status_code=400,
                        response_data={"status_code": 400},
                    )
                elif response.status_code >= 500:
                    raise MCPServiceError(
                        "MCP service internal error",
                        status_code=response.status_code
                    )
                else:
                    raise MCPServiceError(
                        "MCP service error",
                        status_code=response.status_code,
                        response_data={
                            "status_code": response.status_code,
                        },
                    )

        except httpx.TimeoutException:
            logger.error("MCP service request timed out")
            raise MCPServiceError(
                "MCP service request timed out",
                status_code=504
            ) from None
        except httpx.RequestError:
            logger.error("Unable to connect to MCP service")
            raise MCPServiceError(
                "Unable to connect to MCP service",
                status_code=502
            ) from None
        except MCPServiceError:
            raise
        except Exception as e:
            logger.error(
                "Unexpected MCP service failure (%s)", type(e).__name__,
            )
            raise MCPServiceError(
                "MCP service request failed",
                status_code=500
            ) from None


# Global MCP service client instance
mcp_service_client = MCPServiceClient()
