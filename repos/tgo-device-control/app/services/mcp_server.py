"""MCP Server - device proxy with published read-policy enforcement.

This module implements the MCP Streamable HTTP protocol as a transparent
proxy to connected devices. It does NOT define any static tools, nor
perform any tool name mapping or argument transformation. All tool
definitions come from the devices themselves via TCP RPC ``tools/list``.
The published policy narrows tool discovery and calls before forwarding.

The ``device_id`` is resolved from the URL path (``/mcp/{device_id}``).
"""

from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.config import settings
from app.core.logging import get_logger
from app.services.tcp_connection_manager import tcp_connection_manager
from app.schemas.device_access import DeviceServicePrincipal
from app.services.device_session_recording import record_device_tool
from app.services.device_read_policy import allowed_read_arguments

logger = get_logger("services.mcp_server")


class MCPProxy:
    """MCP proxy that narrows discovery and execution to active device policy.

    No static tool definitions, no name mapping, no argument transformation.
    """

    @staticmethod
    def _policy_rejection_reason(
        connection: object, principal: DeviceServicePrincipal | None
    ) -> str | None:
        if getattr(connection, "script_policy_required", False) is True:
            return "Script business execution is not available"
        if principal is None or principal.sub != "tgo-ai":
            return None
        desired_digest = getattr(connection, "desired_read_policy_digest", None)
        if not isinstance(desired_digest, str):
            return "Device policy is not published"
        if principal.read_policy_digest != desired_digest:
            return "Bound device policy changed"
        return None

    # ------------------------------------------------------------------ #
    #  MCP protocol handlers                                              #
    # ------------------------------------------------------------------ #

    def handle_initialize(self) -> Dict[str, Any]:
        """Handle MCP ``initialize`` method."""
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
            },
            "serverInfo": {
                "name": "tgo-device-control",
                "version": settings.SERVICE_VERSION,
            },
        }

    async def handle_list_tools(
        self, device_id: str, principal: DeviceServicePrincipal | None = None
    ) -> Dict[str, Any]:
        """Fetch the connected device's tools within its active policy.

        Args:
            device_id: Target device identifier.

        Returns:
            ``{"tools": [...]}`` with raw tool definitions from the device.
        """
        async with tcp_connection_manager.lifecycle_lock(device_id):
            connection = tcp_connection_manager.get_connection(device_id)
            if not connection:
                logger.warning(f"list_tools: device {device_id} not connected")
                return {"tools": []}

            policy_error = self._policy_rejection_reason(connection, principal)
            if policy_error is not None:
                return {"error": {
                    "code": -32001,
                    "message": policy_error,
                }}

            desired_digest = getattr(connection, "desired_read_policy_digest", None)
            if isinstance(desired_digest, str) and (
                getattr(connection, "applied_read_policy_digest", None)
                != desired_digest
            ):
                return {"error": {
                    "code": -32001,
                    "message": "Device policy is pending acknowledgement",
                }}
            if isinstance(desired_digest, str) and (
                getattr(connection, "read_policy_verified_digest", None)
                != desired_digest
            ):
                return {"error": {
                    "code": -32001,
                    "message": "Device read action requires a current test",
                }}

            raw_tools = await connection.list_tools()
            # Screen negotiation alone is not durable publication or business
            # verification. Keep these tools private until that path exists.
            raw_tools = [tool for tool in raw_tools or [] if not (
                isinstance(tool, dict) and str(tool.get("name", "")).startswith("screen_")
            )]
            if not isinstance(desired_digest, str):
                return {"tools": raw_tools or []}
            read_tools = [
                tool for tool in raw_tools or []
                if isinstance(tool, dict) and tool.get("name") == "fs_read"
            ]
            if not read_tools:
                return {"error": {
                    "code": -32001,
                    "message": "Required device read action is unavailable",
                }}
            return {"tools": read_tools}

    async def handle_call_tool(
        self, device_id: str, params: Dict[str, Any],
        principal: DeviceServicePrincipal | None = None,
    ) -> Dict[str, Any]:
        """Forward tool call to device as-is (no mapping, no transformation).

        Args:
            device_id: Target device identifier.
            params: MCP ``tools/call`` params containing ``name`` and ``arguments``.

        Returns:
            Raw tool call result from the device.
        """
        if principal is not None and principal.sub != "tgo-ai":
            raise HTTPException(403, "Device tool execution requires an AI run")
        name: str = params.get("name", "")
        if principal is not None and principal.sub == "tgo-ai":
            if principal.session_id is None:
                return {
                    "content": [{
                        "type": "text",
                        "text": "Error: Device execution session required",
                    }],
                    "isError": True,
                }
            return await record_device_tool(
                principal, device_id, name,
                lambda: self._forward_tool(device_id, params, principal),
            )
        return await self._forward_tool(device_id, params, principal)

    async def _forward_tool(
        self, device_id: str, params: Dict[str, Any],
        principal: DeviceServicePrincipal | None,
    ) -> Dict[str, Any]:
        name: str = params.get("name", "")
        arguments: Dict[str, Any] = params.get("arguments", {})

        if isinstance(name, str) and name.startswith("screen_"):
            return {"content": [{"type": "text", "text":
                "Error: Screen scope negotiation requires verified publication before business execution"}], "isError": True}

        async with tcp_connection_manager.lifecycle_lock(device_id):
            connection = tcp_connection_manager.get_connection(device_id)
            if not connection:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Error: Device {device_id} is not connected",
                        }
                    ],
                    "isError": True,
                }

            policy_error = self._policy_rejection_reason(connection, principal)
            if policy_error is not None:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Error: {policy_error}",
                    }],
                    "isError": True,
                }

            desired_digest = getattr(connection, "desired_read_policy_digest", None)
            if isinstance(desired_digest, str):
                if (
                    getattr(connection, "applied_read_policy_digest", None)
                    != desired_digest
                ):
                    return {
                        "content": [{
                            "type": "text",
                            "text": "Error: Device policy is pending acknowledgement",
                        }],
                        "isError": True,
                    }
                if (
                    getattr(connection, "read_policy_verified_digest", None)
                    != desired_digest
                ):
                    return {
                        "content": [{
                            "type": "text",
                            "text": "Error: Device read action requires a current test",
                        }],
                        "isError": True,
                    }
                if name != "fs_read" or not allowed_read_arguments(
                    connection.read_policy_directory,
                    connection.read_policy_max_lines,
                    connection.read_policy_max_offset,
                    arguments,
                ):
                    return {
                        "content": [{
                            "type": "text",
                            "text": "Error: Action is outside the active device policy",
                        }],
                        "isError": True,
                    }

            result = await connection.call_tool(name, arguments)
        if result is None:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "Error: Device request failed or timed out",
                    }
                ],
                "isError": True,
            }

        # Return result from device as-is (already in MCP content format)
        return result

    # ------------------------------------------------------------------ #
    #  Unified JSON-RPC dispatcher                                        #
    # ------------------------------------------------------------------ #

    async def handle_jsonrpc(
        self, device_id: str, body: Dict[str, Any],
        principal: DeviceServicePrincipal | None = None,
    ) -> Optional[Dict[str, Any]]:
        """Dispatch a JSON-RPC 2.0 request to the appropriate handler.

        Args:
            device_id: Target device identifier (from URL path).
            body: Parsed JSON-RPC request body.

        Returns:
            JSON-RPC response dict, or ``None`` for notifications.
        """
        method: str = body.get("method", "")
        params: Dict[str, Any] = body.get("params", {})
        request_id = body.get("id")

        # Notifications (no ``id``) – acknowledge silently
        if request_id is None:
            return None

        result: Dict[str, Any]

        if method == "initialize":
            result = self.handle_initialize()
        elif method == "tools/list":
            result = await self.handle_list_tools(device_id, principal)
            if set(result) == {"error"}:
                return {"jsonrpc": "2.0", "id": request_id, "error": result["error"]}
        elif method == "tools/call":
            result = await self.handle_call_tool(device_id, params, principal)
            if set(result) == {"error"}:
                return {"jsonrpc": "2.0", "id": request_id, "error": result["error"]}
        elif method == "ping":
            result = {}
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}",
                },
            }

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result,
        }


# Global singleton
mcp_proxy = MCPProxy()
