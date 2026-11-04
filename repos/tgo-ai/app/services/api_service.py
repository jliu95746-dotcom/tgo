"""API Service client for interacting with the core TGO API service."""

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any, Dict, Optional, TypeVar

import httpx
from pydantic import BaseModel, JsonValue, TypeAdapter

from app.config import settings
from app.exceptions import AuthorizationError, ConflictError, ExternalServiceError
from app.schemas.agent_activation import (
    AgentActivationRequirements, AgentActivationRequirementsQuery,
)
from app.schemas.reply_phase import ReplyPhaseAcknowledgment, ReplyPhaseIdentity
from app.schemas.agent_activation_models import AgentActivationModel, AgentActivationModelQuery
from app.schemas.agent_configuration_actor import AgentConfigurationActor, AgentConfigurationActorQuery
from app.schemas.agent_business_connections import BusinessConnectionOwner, ConnectionOwnerQuery
from app.schemas.agent_business_trials import BusinessTrialCommand, BusinessTrialReceipt
from app.schemas.agent_business_reviews import BusinessTestEvidenceQuery, BusinessTestEvidence
from app.schemas.business_execution_context import BusinessContextCheck, BusinessRequestPrincipal
from app.schemas.agent_business_execution import BusinessExecutionCommand, BusinessExecutionReceipt, BusinessRuntimeQuery, BusinessRuntimeReadiness

logger = logging.getLogger("services.api_service")
json_object = TypeAdapter(dict[str, JsonValue])
ActivationOwnerResponse = TypeVar("ActivationOwnerResponse", bound=BaseModel)


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

    async def get_agent_activation_requirements(
        self, query: AgentActivationRequirementsQuery,
    ) -> AgentActivationRequirements:
        """Fresh owner read: permissions and requirements are never cached."""
        return await self._activation_owner("requirements", query, AgentActivationRequirements)

    async def get_agent_activation_model(
        self, query: AgentActivationModelQuery,
    ) -> AgentActivationModel:
        result = await self._activation_owner("model", query, AgentActivationModel)
        if result.source == "platform" and query.provider_id is not None:
            raise ExternalServiceError("tgo-api", "平台模型要求与员工配置不一致")
        if result.status == "ready" and query.model != "__system_default__" and result.effective_model != query.model:
            raise ExternalServiceError("tgo-api", "当前模型配置与员工选择不一致")
        return result

    async def get_agent_configuration_actor(self, query: AgentConfigurationActorQuery) -> AgentConfigurationActor:
        """Current configuration authority, independent of business resource availability."""
        receipt = await self._activation_owner("actor", query, AgentConfigurationActor)
        if not set(AgentConfigurationActor.model_fields).issubset(receipt.model_fields_set) or receipt.checked_at.tzinfo is None or not -30 <= (datetime.now(UTC) - receipt.checked_at).total_seconds() <= 60:
            raise ExternalServiceError("tgo-api", "无法确认当前员工配置权限，请重试")
        return receipt

    async def get_agent_business_connection(self, query: ConnectionOwnerQuery) -> BusinessConnectionOwner:
        receipt = await self._activation_owner("business-connection", query, BusinessConnectionOwner)
        if (not set(BusinessConnectionOwner.model_fields).issubset(receipt.model_fields_set)
            or any(not set(type(row).model_fields).issubset(row.model_fields_set) for row in receipt.configuration_tests)
            or receipt.checked_at.tzinfo is None or not -30 <= (datetime.now(UTC) - receipt.checked_at).total_seconds() <= 60):
            raise ExternalServiceError("tgo-api", "连接核验结果不完整或已失效")
        return receipt

    async def get_agent_business_test_evidence(self, query: BusinessTestEvidenceQuery) -> BusinessTestEvidence:
        result = await self._activation_owner("business-test-evidence",query,BusinessTestEvidence)
        if (not set(BusinessTestEvidence.model_fields).issubset(result.model_fields_set)
            or result.checked_at.tzinfo is None or not -30 <= (datetime.now(UTC)-result.checked_at).total_seconds() <= 60):
            raise ExternalServiceError("tgo-api", "业务测试客户依据无法确认")
        return result

    async def query_agent_business_trial(self, command: "BusinessTrialCommand") -> "BusinessTrialReceipt":
        from app.services.business_trial_query_client import query
        return await query(self.internal_api_url, command)

    async def get_business_request_context(self, check: BusinessContextCheck) -> BusinessRequestPrincipal:
        from app.services.business_context_client import verify_context
        return await verify_context(self._get_http_client(), self.internal_api_url, check)

    async def query_business_execution(self, command: BusinessExecutionCommand) -> BusinessExecutionReceipt:
        from app.services.business_execution_query_client import query
        return await query(self._get_http_client(), self.internal_api_url, command)

    async def get_business_runtime_readiness(self, query: BusinessRuntimeQuery) -> BusinessRuntimeReadiness:
        return await self._activation_owner("business-runtime", query, BusinessRuntimeReadiness)

    async def _activation_owner(
        self, endpoint: str, query: BaseModel, schema: type[ActivationOwnerResponse],
    ) -> ActivationOwnerResponse:
        """The same bounded private transport verifies every owner identity."""
        try:
            response = await asyncio.wait_for(
                self._get_http_client().post(
                    f"{self.internal_api_url}/ai/activation/{endpoint}",
                    json=query.model_dump(mode="json"),
                    headers={"X-Internal-API-Key": settings.secret_key},
                    follow_redirects=False, timeout=3.0,
                ), timeout=3.0,
            )
        except (httpx.RequestError, TimeoutError):
            raise ExternalServiceError("tgo-api", "无法确认当前员工启用要求，请稍后重试") from None
        if response.status_code in {401, 403}:
            raise AuthorizationError("当前账号没有启用员工的权限")
        if response.status_code == 409:
            raise ConflictError("岗位模板已更新，请刷新并重新检查配置")
        if response.status_code != 200 or len(response.content) > 256 * 1024:
            raise ExternalServiceError("tgo-api", "无法确认当前员工启用要求，请稍后重试")
        try:
            requirements = schema.model_validate_json(response.content)
            identity = requirements.model_dump(include=set(type(query).model_fields))
            if identity != query.model_dump():
                raise ValueError("Activation requirement identity mismatch")
        except ValueError:
            raise ExternalServiceError("tgo-api", "员工启用要求返回不完整，请稍后重试") from None
        return requirements

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
