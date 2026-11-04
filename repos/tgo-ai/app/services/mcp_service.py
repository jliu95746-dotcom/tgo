"""
MCP Service Client

This module provides a client for interacting with the MCP (Model Context Protocol) service.
It handles HTTP requests to the MCP service for tools and other MCP-related operations.
"""

import logging
from typing import Any, Dict, Optional
import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class MCPServiceError(Exception):
    """Exception raised when MCP service operations fail."""

    def __init__(self, message: str, status_code: Optional[int] = None, response_data: Optional[Dict[str, Any]] = None):
        self.message = message
        self.status_code = status_code
        self.response_data = response_data
        super().__init__(message)


class MCPServiceClient:
    """Client for interacting with the MCP service."""

    def __init__(self):
        self.base_url = settings.mcp_service_url.rstrip('/')
        self.timeout = 30.0

    def _get_headers(self, additional_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
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
    ) -> Dict[str, Any]:
        """
        Make a request to the MCP service.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            path: API path (e.g., "/v1/tools")
            params: Optional query parameters
            json_data: Optional JSON data for request body
            headers: Optional additional headers

        Returns:
            Dict[str, Any]: The JSON response from the MCP service

        Raises:
            MCPServiceError: If the request fails
        """
        url = f"{self.base_url}{path}"
        request_headers = self._get_headers(headers)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                logger.info(f"Making {method} request to MCP service: {url}")

                response = await client.request(
                    method=method,
                    url=url,
                    headers=request_headers,
                    params=params,
                    json=json_data
                )

                logger.info(f"MCP service responded with status: {response.status_code}")

                # Handle different response status codes
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 201:
                    return response.json()
                elif response.status_code == 204:
                    return {}
                elif response.status_code == 404:
                    raise MCPServiceError(
                        "Resource not found in MCP service",
                        status_code=404,
                        response_data=response.json() if response.content else None
                    )
                elif response.status_code == 400:
                    error_data = response.json() if response.content else {}
                    raise MCPServiceError(
                        f"Bad request to MCP service: {error_data.get('detail', 'Invalid request')}",
                        status_code=400,
                        response_data=error_data
                    )
                elif response.status_code >= 500:
                    raise MCPServiceError(
                        "MCP service internal error",
                        status_code=response.status_code
                    )
                else:
                    error_data = response.json() if response.content else {}
                    raise MCPServiceError(
                        f"MCP service error: {error_data.get('detail', 'Unknown error')}",
                        status_code=response.status_code,
                        response_data=error_data
                    )

        except httpx.TimeoutException:
            logger.error(f"Timeout when calling MCP service: {url}")
            raise MCPServiceError(
                "MCP service request timed out",
                status_code=504
            )
        except httpx.ConnectError:
            logger.error(f"Connection error when calling MCP service: {url}")
            raise MCPServiceError(
                "Unable to connect to MCP service",
                status_code=502
            )
        except MCPServiceError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error when calling MCP service: {e}")
            raise MCPServiceError(
                f"MCP service request failed: {str(e)}",
                status_code=500
            )


# Global MCP service client instance
mcp_service_client = MCPServiceClient()