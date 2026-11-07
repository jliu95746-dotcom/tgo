"""Plugin Tool Sync Service - Sync plugin tools to tgo-ai service."""

from typing import Any, Dict

import httpx

from app.config import settings
from app.core.logging import get_logger
from app.services.plugin_manager import PluginConnection
from app.services.plugin_failures import log_plugin_failure
from app.domain.business_tools.target_transport import business_query_log_scope

logger = get_logger("services.tool_sync")


class PluginToolSyncService:
    """Service for synchronizing plugin MCP tools to the AI service."""

    def __init__(self):
        self.ai_base_url = settings.AI_SERVICE_URL.rstrip("/")
        self.timeout = settings.AI_SERVICE_TIMEOUT

    async def sync_plugin_tools(self, plugin: PluginConnection):
        (
            "\n        Sync plugin tools to tgo-ai when a plugin connects.\n  "
            "      \n        This creates/updates tool records in tgo-ai for e"
            "ach MCP tool the plugin provides.\n        "
        )
        if not plugin.project_id:
            logger.info("Skipping plugin tool sync: no project associated")
            return

        logger.info("Syncing plugin tools")

        for cap in plugin.capabilities:
            if cap.type == "mcp_tools" and cap.tools:
                for tool in cap.tools:
                    tool_data = {
                        "name": f"plugin:{plugin.id}:{tool.name}",
                        "description": tool.description or tool.title,
                        "tool_type": "MCP",
                        "transport_type": "plugin",
                        "endpoint": f"plugin://{plugin.id}",
                        "project_id": plugin.project_id,  # Add project_id here
                        "config": {
                            "plugin_id": plugin.id,
                            "tool_name": tool.name,
                            "parameters": [
                                p.model_dump() for p in (tool.parameters or [])
                            ],
                        },
                    }
                    await self._register_tool(tool_data)

    async def remove_plugin_tools(self, plugin_id: str):
        """
        Remove plugin tools from tgo-ai when a plugin disconnects.
        """
        logger.info("Removing plugin tools")
        await self._delete_tools_by_prefix(f"plugin:{plugin_id}:")

    async def _register_tool(self, tool_data: Dict[str, Any]):
        """Register a tool in tgo-ai (create or update across all projects)."""
        url = f"{self.ai_base_url}/api/v1/tools/plugin"

        try:
            with business_query_log_scope():
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        url,
                        json=tool_data,
                        headers={"Content-Type": "application/json"},
                    )

                if response.status_code in (200, 201):
                    logger.info("Synced plugin tool")
                else:
                    logger.warning(
                        "Failed to sync plugin tool",
                        extra={"status_code": response.status_code},
                    )
        except Exception as e:
            log_plugin_failure("sync_plugin_tool", e)

    async def _delete_tools_by_prefix(self, prefix: str):
        """Delete tools with names starting with prefix."""
        url = f"{self.ai_base_url}/api/v1/tools/plugin/by-prefix"

        try:
            with business_query_log_scope():
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.delete(
                        url,
                        params={"prefix": prefix},
                    )

                if response.status_code in (200, 204):
                    logger.info("Removed plugin tools")
                else:
                    logger.warning(
                        "Failed to remove plugin tools",
                        extra={"status_code": response.status_code},
                    )
        except Exception as e:
            log_plugin_failure("remove_plugin_tools", e)


# Global instance
plugin_tool_sync = PluginToolSyncService()


def setup_tool_sync():
    """Setup tool sync with plugin manager."""
    from app.services.plugin_manager import plugin_manager

    plugin_manager.set_tool_sync(plugin_tool_sync)
