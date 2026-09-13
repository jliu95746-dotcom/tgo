"""API Service client for interacting with the core TGO API service."""

import asyncio
import logging
import time
from typing import Any, Dict, Optional

import httpx
from pydantic import JsonValue, TypeAdapter

from app.config import settings
from app.schemas.reply_phase import ReplyPhaseAcknowledgment, ReplyPhaseIdentity

logger = logging.getLogger("services.api_service")
json_object = TypeAdapter(dict[str, JsonValue])


class APIServiceClient:
    """Client for interacting with the core TGO API service."""

    def __init__(self) -> None:
        """Initialize the API service client."""
        # Use docker service name instead of localhost for internal communication
        self.api_base_url = settings.api_service_url
        internal_base = (settings.api_internal_service_url or self.api_base_url).rstrip(
            "/"
        )
        self.internal_api_url = f"{internal_base}/internal"

        self.plugin_runtime_url = settings.plugin_runtime_url
        self.timeout = 30.0
        self._http_client: Optional[httpx.AsyncClient] = None
        self._credential_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}
        self._credential_cache_ttl_seconds = 300.0

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=self.timeout,
                trust_env=False,
            )
        return self._http_client

    async def aclose(self) -> None:
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def confirm_reply_phase_ended(self, phase: ReplyPhaseIdentity) -> bool:
        """Bounded, idempotent receipt delivery through the private API port."""
        payload = {**phase.model_dump(mode="json"), "status": "ended"}
        for attempt in range(2):
            try:
                response = await self._get_http_client().post(
                    f"{self.internal_api_url}/ai/reply-phases/ended",
                    json=payload,
                    timeout=1.5,
                )
                if response.status_code == 200:
                    ack = ReplyPhaseAcknowledgment.model_validate_json(response.content)
                    return ack.phase_id == phase.phase_id
                if response.status_code < 500 and response.status_code != 429:
                    return False
            except (httpx.RequestError, ValueError):
                pass
            if attempt == 0:
                await asyncio.sleep(0.05)
        logger.warning("AI phase end receipt could not be confirmed")
        return False

    async def get_store_credential(self, project_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch store credential for a project from the internal API.
        """
        cached = self._credential_cache.get(project_id)
        now = time.monotonic()
        if cached and cached[0] > now:
            return cached[1]

        url = f"{self.internal_api_url}/store/{project_id}/credential"
        try:
            response = await self._get_http_client().get(url)
            if response.status_code == 200:
                credential = json_object.validate_json(response.content)
                self._credential_cache[project_id] = (
                    now + self._credential_cache_ttl_seconds,
                    credential,
                )
                return credential
        except Exception:
            pass

        logger.error(
            "Failed to fetch store credential for project %s",
            project_id,
        )
        return None

    async def execute_plugin_tool(
        self,
        plugin_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        context: Dict[str, Any],
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute a plugin tool via the TGO Plugin Runtime service.

        Args:
            plugin_id: Unique plugin ID
            tool_name: Name of the tool to execute
            arguments: Tool arguments from LLM
            context: Context containing user_id, session_id, agent_id, etc.

        Returns:
            Tool result dictionary
        """
        if not project_id or not project_id.strip():
            raise ValueError("Plugin execution requires a project ID")
        url = f"{self.plugin_runtime_url}/plugins/tools/execute/{plugin_id}/{tool_name}"

        payload = {
            "arguments": arguments,
            "context": context,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    url,
                    json=payload,
                    params={"project_id": project_id},
                    headers={"Content-Type": "application/json"},
                )

                if response.status_code == 200:
                    return json_object.validate_json(response.content)
                else:
                    logger.error(
                        "API service error executing plugin tool: %s %s",
                        response.status_code,
                        response.text,
                    )
                    return {
                        "success": False,
                        "error": f"API service error: {response.status_code}",
                        "content": f"工具执行失败 (HTTP {response.status_code})",
                    }

            except ValueError:
                logger.error("Plugin tool returned an invalid JSON object")
                return {
                    "success": False,
                    "error": "Invalid plugin response format",
                    "content": "工具返回的数据格式不正确",
                }
            except httpx.RequestError as e:
                logger.error(f"Unable to connect to API service: {str(e)}")
                return {
                    "success": False,
                    "error": str(e),
                    "content": "无法连接到 TGO API 服务",
                }


# Global API service client instance
api_service_client = APIServiceClient()
