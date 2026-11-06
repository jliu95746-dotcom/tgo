"""AI service client for proxying requests to external AI service."""

import json
from collections.abc import AsyncGenerator, Mapping
from typing import Dict, List, Optional
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException
from pydantic import JsonValue, TypeAdapter, ValidationError

from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.agent_activation import (
    AgentActivationPolicies,
    AgentActivationPolicyChange,
    AgentActivationPolicyRecord,
)
from app.schemas.agent_activation_readiness import AgentActivationReadiness
from app.schemas.agent_activation_requests import (
    AgentActivationReceipt,
    AgentActivationRequest,
)
from app.schemas.agent_business_connections import (
    AgentBusinessConnectionRecord,
    AgentBusinessConnections,
    AgentBusinessConnectionWrite,
)
from app.schemas.agent_device_script_binding import (
    AgentDeviceScriptRecord,
    AgentDeviceScriptState,
    AgentDeviceScriptWrite,
)
from app.schemas.agent_device_script_cases import (
    ScriptCasePage,
    ScriptCaseRead,
    ScriptCaseRecord,
    ScriptCaseSaveControl,
    ScriptCaseSaveState,
    ScriptCaseTrialResult,
    ScriptCaseWrite,
    ScriptTrialEvidence,
)
from app.schemas.agent_device_script_requests import (
    ScriptTrialControl,
    ScriptTrialRequestState,
)
from app.schemas.agent_device_script_reviews import (
    ScriptReviewState,
    ScriptReviewWrite,
    ScriptRunCheck,
)
from app.schemas.agent_device_script_runs import (
    ScriptRunControl,
    ScriptRunStart,
    ScriptRunView,
)
from app.schemas.agent_device_script_selection import (
    ScriptSelectionRecord,
    ScriptSelectionState,
    ScriptSelectionWrite,
)
from app.schemas.agent_device_script_trials import (
    ScriptTrialReport,
    ScriptTrialStart,
)
from app.schemas.agent_execution import (
    AgentExecutionCheckReceipt,
    AgentExecutionReference,
)
from app.schemas.agent_knowledge_capabilities import (
    AgentKnowledgeCapabilities,
    KnowledgeCapabilityQuery,
)
from app.schemas.agent_trial_reviews import (
    KnowledgeReviewCommand,
    KnowledgeReviewQuery,
    KnowledgeReviewResponse,
    KnowledgeReviewState,
)
from app.schemas.agent_trials import AgentTrialReport, AgentTrialRequest
from app.schemas.ai import (
    AgentDeviceActionReadiness,
    WorkflowPinTransitionHistory,
    WorkflowPinTransitionReceipt,
    WorkflowReleaseAuditResponse,
)
from app.schemas.assist_trace import AssistTracePhase
from app.schemas.business_execution_context import BusinessRequestAuthorization
from app.schemas.humanization import (
    ConversationTurn,
    HumanizationContext,
    HumanizationMatchRequest,
    TrainingPublishRequest,
    TrainingReview,
)
from app.schemas.knowledge import KnowledgeChannel
from app.schemas.knowledge_availability import AgentKnowledgeAvailability
from app.schemas.media_probe import MediaProbeResult
from app.schemas.reply_phase import ReplyPhaseIdentity
from app.schemas.scenario_agents import (
    ScenarioAgentReceipt,
    ScenarioAgentSnapshot,
)
from app.schemas.tool_probe import MCPDiscoverRequest, MCPDiscoverResponse
from app.schemas.workflow_ai_usage import WorkflowAgentInput, WorkflowAIResult
from app.services.ai_transport import AITransport, JsonObject, RequestParams
from app.services.assist_run_trace import record_assist_run_trace
from app.services.business_execution_transport import business_headers

logger = get_logger("ai_client")


class AIServiceClient(AITransport):
    """Client for communicating with the external AI service."""

    def __init__(self) -> None:
        self.base_url = str(settings.AI_SERVICE_URL).rstrip("/")
        self.timeout = settings.AI_SERVICE_TIMEOUT
        self.api_key = settings.AI_SERVICE_API_KEY

    async def run_workflow_agent(
        self,
        payload: WorkflowAgentInput,
        project_id: UUID,
        execution_id: UUID,
        lineage: tuple[str, ...],
    ) -> WorkflowAIResult:
        from app.services.workflow_ai_client import run

        return await run(self, payload, project_id, execution_id, lineage)

    # Agent endpoints
    @staticmethod
    def _execution_headers(
        project_id: str, channel: str | None
    ) -> dict[str, str]:
        """Derive execution scope from the customer's platform in the API."""
        return {
            "X-Internal-API-Key": settings.SECRET_KEY,
            "X-Project-Id": str(UUID(project_id)),
            "X-Execution-Channel": KnowledgeChannel(
                channel or "internal"
            ).value,
        }

    async def run_supervisor_agent(
        self,
        message: str,
        project_id: str,
        *,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        stream: bool = False,
        mcp_url: Optional[str] = None,
        rag_url: Optional[str] = None,
        knowledge_channel: Optional[str] = None,
        system_message: Optional[str] = None,
        enable_memory: Optional[bool] = None,
        disable_tools: Optional[bool] = None,
        knowledge_only: bool = False,
        response_purpose: str = "standard",
        require_current_knowledge: bool = False,
        knowledge_context: list[str] | None = None,
        markdown: Optional[bool] = None,
        temperature: Optional[float] = None,
        cancel_on_disconnect: bool = False,
        reply_phase: ReplyPhaseIdentity | None = None,
        business_authorization: "BusinessRequestAuthorization | None" = None,
        trace_phase: AssistTracePhase | None = None,
        trace_template: str | None = None,
    ) -> JsonObject:
        """Run supervisor agent workflow and return response content."""
        payload: dict[str, object] = {
            "message": message,
            "stream": stream,
        }
        if agent_id:
            payload["agent_id"] = agent_id
        if session_id:
            payload["session_id"] = session_id
        if user_id:
            payload["user_id"] = user_id
        if mcp_url:
            payload["mcp_url"] = mcp_url
        if rag_url:
            payload["rag_url"] = rag_url
        if knowledge_channel is not None:
            payload["knowledge_channel"] = knowledge_channel
        if system_message is not None:
            payload["system_message"] = system_message
        if enable_memory is not None:
            payload["enable_memory"] = enable_memory
        if disable_tools is not None:
            payload["disable_tools"] = disable_tools
        if knowledge_only:
            payload["knowledge_only"] = True
            payload["ui_mode"] = "text"
        payload["response_purpose"] = response_purpose
        if require_current_knowledge:
            payload["require_current_knowledge"] = True
            payload["knowledge_context"] = (knowledge_context or [])[-4:]
        if response_purpose == "expression":
            payload["ui_mode"] = "text"
        if markdown is not None:
            payload["markdown"] = markdown
        if temperature is not None:
            payload["temperature"] = temperature
        if cancel_on_disconnect:
            payload["cancel_on_disconnect"] = True
        if reply_phase is not None:
            if (
                not cancel_on_disconnect
                or reply_phase.project_id != project_id
            ):
                raise ValueError("Invalid AI reply phase ownership")
            payload["reply_phase"] = reply_phase.model_dump(mode="json")

        response = await self._make_request(
            "POST",
            "/api/v1/agents/run",
            json_data=payload,
            params={"project_id": project_id},
            extra_headers={
                **self._execution_headers(project_id, knowledge_channel),
                **business_headers(
                    business_authorization,
                    project_id,
                    user_id,
                    knowledge_channel,
                ),
            },
        )
        data = await self._handle_response(response)
        if isinstance(data, dict):
            if data.get("success") is False:
                raise HTTPException(502, "AI 生成失败，请稍后重试或转人工处理")
            record_assist_run_trace(
                trace_phase,
                data,
                system_message=system_message,
                template_text=trace_template,
                requested_agent_id=agent_id,
            )
            return data

        return {"content": data}

    async def run_supervisor_agent_stream(
        self,
        message: str,
        project_id: str,
        *,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        mcp_url: Optional[str] = None,
        rag_url: Optional[str] = None,
        knowledge_channel: Optional[str] = None,
        enable_memory: Optional[bool] = None,
        system_message: Optional[str] = None,
        expected_output: Optional[str] = None,
        excluded_tool_ids: Optional[List[str]] = None,
        disable_tools: bool | None = None,
        expected_device_id: str | None = None,
        require_current_knowledge: bool = False,
        knowledge_context: list[str] | None = None,
        cancel_on_disconnect: bool = False,
        reply_phase: ReplyPhaseIdentity | None = None,
        business_authorization: "BusinessRequestAuthorization | None" = None,
    ) -> AsyncGenerator[tuple[str, JsonValue], None]:
        """Stream supervisor agent events as they arrive."""
        payload: dict[str, object] = {
            "message": message,
            "stream": True,
            "ui_mode": "text",
        }
        if disable_tools is not None:
            payload["disable_tools"] = disable_tools
        if require_current_knowledge:
            payload["require_current_knowledge"] = True
            payload["knowledge_context"] = (knowledge_context or [])[-4:]
        if agent_id:
            payload["agent_id"] = agent_id
        if session_id:
            payload["session_id"] = session_id
        if user_id:
            payload["user_id"] = user_id
        if mcp_url:
            payload["mcp_url"] = mcp_url
        if rag_url:
            payload["rag_url"] = rag_url
        if knowledge_channel is not None:
            payload["knowledge_channel"] = knowledge_channel
        if enable_memory is not None:
            payload["enable_memory"] = enable_memory
        if system_message is not None:
            payload["system_message"] = system_message
        if expected_output is not None:
            payload["expected_output"] = expected_output
        if excluded_tool_ids:
            payload["excluded_tool_ids"] = excluded_tool_ids
        if expected_device_id is not None:
            payload["expected_device_id"] = str(UUID(expected_device_id))
        if cancel_on_disconnect:
            payload["cancel_on_disconnect"] = True
        if reply_phase is not None:
            if (
                not cancel_on_disconnect
                or reply_phase.project_id != project_id
            ):
                raise ValueError("Invalid AI reply phase ownership")
            payload["reply_phase"] = reply_phase.model_dump(mode="json")

        url = f"{self.base_url}/api/v1/agents/run"
        request_id = str(uuid4())
        headers = self._get_headers()
        headers.update(self._execution_headers(project_id, knowledge_channel))
        headers.update(
            business_headers(
                business_authorization, project_id, user_id, knowledge_channel
            )
        )
        headers["X-Request-ID"] = request_id
        headers.setdefault("Accept", "text/event-stream")

        logger.info(
            "AI service stream request: POST",
            extra={"request_id": request_id},
        )

        try:
            async with httpx.AsyncClient(
                timeout=None,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "POST",
                    url,
                    headers=headers,
                    json=self._to_jsonable(payload),
                    params={"project_id": project_id},
                ) as response:
                    if response.status_code != 200:
                        await response.aread()
                        error_data = self._error_detail(response)
                        logger.warning(
                            "AI service stream error: %s",
                            response.status_code,
                            extra={"request_id": request_id},
                        )
                        raise HTTPException(
                            status_code=response.status_code,
                            detail=error_data,
                        )

                    event_name: Optional[str] = None
                    data_lines: List[str] = []

                    async for line in response.aiter_lines():
                        if not line:
                            if not data_lines:
                                event_name = None
                                continue
                            data_text = "\n".join(data_lines)
                            parsed = self._event_data(data_text)
                            yield (event_name or "message", parsed)
                            event_name = None
                            data_lines = []
                            continue
                        if line.startswith(":"):
                            continue
                        if line.startswith("event:"):
                            event_name = line.split(":", 1)[1].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line.split(":", 1)[1].strip())

                    if data_lines:
                        data_text = "\n".join(data_lines)
                        parsed = self._event_data(data_text)
                        yield (event_name or "message", parsed)
        except httpx.TimeoutException:
            logger.error(
                "AI service stream timeout", extra={"request_id": request_id}
            )
            raise HTTPException(
                status_code=504, detail="AI service stream timed out"
            )
        except httpx.RequestError as exc:
            logger.error(
                "AI service stream request error",
                extra={
                    "request_id": request_id,
                    "error_type": type(exc).__name__,
                },
            )
            raise HTTPException(
                status_code=502, detail="Failed to connect to AI service"
            )

    async def cancel_supervisor_run(
        self,
        project_id: str,
        run_id: str,
        reason: Optional[str] = None,
    ) -> JsonValue:
        """Cancel a running supervisor execution by run_id.
        Proxies to AI service: POST /api/v1/agents/run/{run_id}/cancel
        """
        body = {"reason": reason} if reason else None
        response = await self._make_request(
            "POST",
            f"/api/v1/agents/run/{run_id}/cancel",
            json_data=body,
            params={"project_id": project_id},
        )
        return await self._handle_response(response)

    async def check_agents_exist(
        self,
        project_id: str,
    ) -> bool:
        """
        Check if any agents exist for the specified project.

        Args:
            project_id: Project ID to check

        Returns:
            True if agents exist, False otherwise
        """
        try:
            response = await self._make_request(
                "GET",
                "/api/v1/agents/exists",
                params={"project_id": project_id},
            )
            data = await self._handle_response(response)
            if isinstance(data, dict):
                exists = data.get("exists", False)
                return exists if isinstance(exists, bool) else False
            return False
        except HTTPException:
            # If the check fails, assume agents exist to avoid blocking
            logger.warning(
                "Failed to check agents existence; assuming agents exist"
            )
            return True
        except Exception as e:
            logger.warning(
                "Unexpected agents existence error; assuming agents exist",
                extra={"error_type": type(e).__name__},
            )
            return True

    async def list_agents(
        self,
        project_id: str,
        model: Optional[str] = None,
        is_default: Optional[bool] = None,
        limit: int = 20,
        offset: int = 0,
        bound_device_id: UUID | None = None,
    ) -> JsonObject:
        """List agents from AI service."""
        params: RequestParams = {
            "limit": limit,
            "offset": offset,
            "project_id": project_id,
        }
        if model:
            params["model"] = model
        if is_default is not None:
            params["is_default"] = is_default
        if bound_device_id is not None:
            params["bound_device_id"] = str(bound_device_id)

        response = await self._make_request(
            "GET", "/api/v1/agents", params=params
        )
        return await self._object_response(response)

    async def get_device_action_readiness(
        self,
        project_id: str,
        agent_id: str,
        *,
        requested_by: UUID,
    ) -> AgentDeviceActionReadiness:
        response = await self._make_request(
            "GET",
            f"/api/v1/agents/{agent_id}/device-action-readiness",
            params={"project_id": project_id},
            extra_headers=self._trial_headers(UUID(project_id), requested_by),
        )
        try:
            return AgentDeviceActionReadiness.model_validate(
                await self._handle_response(response)
            )
        except ValidationError:
            raise HTTPException(502, "AI 员工设备状态响应无效") from None

    async def get_workflow_release_audit(
        self,
        project_id: str,
        limit: int,
        offset: int,
    ) -> WorkflowReleaseAuditResponse:
        """Read the project's unpinned employee workflow bindings."""
        response = await self._make_request(
            "GET",
            "/api/v1/agents/workflow-release-audit",
            params={
                "project_id": project_id,
                "limit": limit,
                "offset": offset,
            },
        )
        return WorkflowReleaseAuditResponse.model_validate(
            await self._handle_response(response)
        )

    async def pin_legacy_workflow(
        self,
        *,
        project_id: str,
        agent_id: str,
        workflow_id: str,
        command: dict[str, object],
        requested_by: UUID,
    ) -> WorkflowPinTransitionReceipt:
        """Forward a signed administrator command for one old binding."""
        response = await self._make_request(
            "POST",
            f"/api/v1/agents/{agent_id}/workflows/{workflow_id}/pin-legacy",
            json_data=command,
            params={"project_id": project_id},
            extra_headers=self._trial_headers(UUID(project_id), requested_by),
        )
        return WorkflowPinTransitionReceipt.model_validate(
            await self._handle_response(response)
        )

    async def get_workflow_pin_transitions(
        self,
        *,
        project_id: str,
        agent_id: str,
        requested_by: UUID,
        limit: int,
        offset: int,
    ) -> WorkflowPinTransitionHistory:
        response = await self._make_request(
            "GET",
            f"/api/v1/agents/{agent_id}/workflow-pin-transitions",
            params={
                "project_id": project_id,
                "limit": limit,
                "offset": offset,
            },
            extra_headers=self._trial_headers(UUID(project_id), requested_by),
        )
        return WorkflowPinTransitionHistory.model_validate(
            await self._handle_response(response)
        )

    async def create_agent(
        self,
        project_id: str,
        agent_data: Mapping[str, object],
        *,
        requested_by: UUID | None = None,
    ) -> JsonObject:
        """Create agent in AI service."""
        response = await self._make_request(
            "POST",
            "/api/v1/agents",
            json_data=agent_data,
            params={"project_id": project_id},
            extra_headers=self._trial_headers(UUID(project_id), requested_by)
            if requested_by
            else None,
        )
        return await self._object_response(response)

    async def get_agent(
        self,
        project_id: str,
        agent_id: str,
        include_tools: bool = True,
        include_collections: bool = False,
        include_workflows: bool = False,
    ) -> JsonObject:
        """Get agent from AI service."""
        params: RequestParams = {
            "include_tools": include_tools,
            "include_collections": include_collections,
            "include_workflows": include_workflows,
            "project_id": project_id,
        }
        response = await self._make_request(
            "GET", f"/api/v1/agents/{agent_id}", params=params
        )
        return await self._object_response(response)

    async def knowledge_availability(
        self, project_id: str, channel: str, agent_id: UUID | None = None
    ) -> AgentKnowledgeAvailability:
        params = {"project_id": project_id, "channel": channel}
        if agent_id is not None:
            params["agent_id"] = str(agent_id)
        response = await self._make_request(
            "GET", "/api/v1/agents/knowledge-availability", params=params
        )
        return AgentKnowledgeAvailability.model_validate(
            await self._handle_response(response)
        )

    async def update_agent(
        self,
        project_id: str,
        agent_id: str,
        agent_data: Mapping[str, object],
        *,
        requested_by: UUID | None = None,
    ) -> JsonObject:
        """Update agent in AI service."""
        response = await self._make_request(
            "PATCH",
            f"/api/v1/agents/{agent_id}",
            json_data=agent_data,
            params={"project_id": project_id},
            extra_headers=self._trial_headers(UUID(project_id), requested_by)
            if requested_by
            else None,
        )
        return await self._object_response(response)

    async def delete_agent(
        self,
        project_id: str,
        agent_id: str,
    ) -> None:
        """Delete agent from AI service."""
        response = await self._make_request(
            "DELETE",
            f"/api/v1/agents/{agent_id}",
            params={"project_id": project_id},
        )
        await self._handle_response(response)

    async def set_agent_tool_enabled(
        self,
        project_id: str,
        agent_id: str,
        tool_id: str,
        enabled: bool,
    ) -> None:
        """Enable or disable a specific tool binding for an agent."""
        response = await self._make_request(
            "PATCH",
            f"/api/v1/agents/{agent_id}/tools/{tool_id}/enabled",
            json_data={"enabled": enabled},
            params={"project_id": project_id},
        )
        # 204 No Content response
        if response.status_code == 204:
            return None
        await self._handle_response(response)

    async def set_agent_collection_enabled(
        self,
        project_id: str,
        agent_id: str,
        collection_id: str,
        enabled: bool,
    ) -> None:
        """Enable or disable a specific collection binding for an agent."""
        response = await self._make_request(
            "PATCH",
            f"/api/v1/agents/{agent_id}/collections/{collection_id}/enabled",
            json_data={"enabled": enabled},
            params={"project_id": project_id},
        )
        # 204 No Content response
        if response.status_code == 204:
            return None
        await self._handle_response(response)

    async def set_agent_workflow_enabled(
        self,
        project_id: str,
        agent_id: str,
        workflow_id: str,
        enabled: bool,
    ) -> None:
        """Enable or disable a specific workflow binding for an agent."""
        response = await self._make_request(
            "PATCH",
            f"/api/v1/agents/{agent_id}/workflows/{workflow_id}/enabled",
            json_data={"enabled": enabled},
            params={"project_id": project_id},
        )
        # 204 No Content response
        if response.status_code == 204:
            return None
        await self._handle_response(response)

    async def clear_session_memory(
        self,
        project_id: str,
        session_id: str,
        user_id: Optional[str] = None,
    ) -> None:
        """Clear memory for a session in the AI service."""
        response = await self._make_request(
            "DELETE",
            f"/api/v1/agents/sessions/{session_id}/memory",
            params={"project_id": project_id, "user_id": user_id},
        )
        await self._handle_response(response)

    # Tools endpoints
    async def list_tools(
        self,
        project_id: str,
        tool_type: Optional[str] = None,
        include_deleted: bool = False,
    ) -> list[JsonObject]:
        """List tools from AI service."""
        params: RequestParams = {
            "project_id": project_id,
            "include_deleted": include_deleted,
        }
        if tool_type:
            params["tool_type"] = tool_type

        response = await self._make_request(
            "GET", "/api/v1/tools", params=params
        )
        return await self._object_list_response(response)

    async def create_tool(
        self,
        tool_data: Mapping[str, object],
    ) -> JsonObject:
        """Create tool in AI service."""
        response = await self._make_request(
            "POST", "/api/v1/tools", json_data=tool_data
        )
        return await self._object_response(response)

    async def update_tool(
        self,
        project_id: str,
        tool_id: str,
        tool_data: Mapping[str, object],
    ) -> JsonObject:
        """Update tool in AI service."""
        response = await self._make_request(
            "PATCH",
            f"/api/v1/tools/{tool_id}",
            json_data=tool_data,
            params={"project_id": project_id},
        )
        return await self._object_response(response)

    async def delete_tool(
        self,
        project_id: str,
        tool_id: str,
    ) -> JsonObject:
        """Delete tool from AI service (soft delete)."""
        response = await self._make_request(
            "DELETE",
            f"/api/v1/tools/{tool_id}",
            params={"project_id": project_id},
        )
        return await self._object_response(response)

    async def create_or_update_tool(
        self,
        project_id: str,
        tool_data: Mapping[str, object],
    ) -> JsonObject:
        """Create or update a tool in the AI service."""
        tool_name = tool_data.get("name")
        # Check if tool already exists
        existing_tools = await self.list_tools(project_id)
        existing_tool = next(
            (t for t in existing_tools if t["name"] == tool_name), None
        )

        if existing_tool:
            # Update existing tool
            tool_id = existing_tool["id"]
            if not isinstance(tool_id, str):
                raise HTTPException(
                    502, "AI tool service returned an invalid identity"
                )
            return await self.update_tool(project_id, tool_id, tool_data)
        else:
            # Create new tool
            # AI tool creation requires the owning project in the payload.
            data = {**tool_data, "project_id": project_id}
            return await self.create_tool(data)

    async def delete_tools_by_prefix(
        self,
        project_id: str,
        prefix: str,
    ) -> None:
        """Delete all tools with names starting with prefix."""
        tools = await self.list_tools(project_id)
        for tool in tools:
            name, tool_id = tool["name"], tool["id"]
            if not isinstance(name, str) or not isinstance(tool_id, str):
                raise HTTPException(
                    502, "AI tool service returned an invalid identity"
                )
            if name.startswith(prefix):
                await self.delete_tool(project_id, tool_id)

    async def discover_tools(
        self, project_id: str, request: "MCPDiscoverRequest"
    ) -> "MCPDiscoverResponse":
        from app.schemas.tool_probe import MCPDiscoverResponse

        response = await self._make_request(
            "POST",
            "/api/v1/tools/discover",
            json_data=request.model_dump(),
            extra_headers={
                "X-Internal-API-Key": settings.SECRET_KEY,
                "X-Project-Id": project_id,
            },
        )
        return MCPDiscoverResponse.model_validate(
            await self._handle_response(response)
        )

    async def execute_tool(
        self,
        *,
        project_id: str,
        tool_id: str,
        input_data: Mapping[str, object],
        visitor_id: Optional[str] = None,
    ) -> JsonObject:
        """Execute a project-owned tool directly through tgo-ai."""

        payload: dict[str, object] = {"input_data": input_data}
        if visitor_id is not None:
            payload["visitor_id"] = visitor_id
        response = await self._make_request(
            "POST",
            f"/api/v1/tools/{tool_id}/execute",
            json_data=payload,
            extra_headers={
                "X-Internal-API-Key": settings.SECRET_KEY,
                "X-Project-Id": project_id,
            },
        )
        result = await self._handle_response(response)
        if not isinstance(result, dict):
            raise HTTPException(
                status_code=502,
                detail="AI tool service returned an invalid response",
            )
        return result

    # Chat completions endpoint
    async def chat_completions(
        self,
        project_id: str,
        provider_id: str,
        model: str,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> JsonObject:
        """
        Create a chat completion using the AI service.

        This proxies to the AI service's /api/v1/chat/completions endpoint,
        which is compatible with OpenAI's Chat Completions API format.

        Args:
            project_id: Project ID for authorization
            provider_id: UUID of the LLM provider to use
            model: Model identifier (e.g., 'gpt-4', 'claude-3-opus')
            messages: List of conversation messages with 'role' and 'content'
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens to generate
            stream: Whether to stream the response (not supported yet)

        Returns:
            Chat completion response with choices
        """
        payload: dict[str, object] = {
            "provider_id": provider_id,
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        response = await self._make_request(
            "POST",
            "/api/v1/chat/completions",
            json_data=payload,
            params={"project_id": project_id},
        )
        return await self._object_response(response)

    async def probe_media_model(
        self,
        *,
        project_id: str,
        provider_id: str,
        model_id: str,
        capability: str,
        content: bytes,
        mime_type: str,
    ) -> "MediaProbeResult":
        response = await self._make_request(
            "POST",
            "/api/v1/analysis/probe",
            extra_headers={
                "X-Internal-API-Key": settings.SECRET_KEY,
                "X-Project-Id": project_id,
            },
            form_data={
                "provider_id": provider_id,
                "model_id": model_id,
                "capability": capability,
            },
            files={"file": ("sample", content, mime_type)},
        )
        if response.status_code != 200 or len(response.content) > 512 * 1024:
            raise HTTPException(502, "识别测试服务暂时不可用，请检查 AI 服务和配置同步状态。")
        try:
            return MediaProbeResult.model_validate_json(response.content)
        except ValidationError as exc:
            raise HTTPException(502, "测试服务返回了无效结果。") from exc

    async def analyze_media(
        self,
        *,
        project_id: str,
        metadata: dict[str, JsonValue],
        content: bytes,
        mime_type: str,
    ) -> dict[str, JsonValue]:
        """Send verified bytes to the configured project media models."""
        response = await self._make_request(
            "POST",
            "/api/v1/analysis/media",
            extra_headers={
                "X-Internal-API-Key": settings.SECRET_KEY,
                "X-Project-Id": project_id,
            },
            form_data={"metadata": json.dumps(metadata, ensure_ascii=False)},
            files={"file": ("media", content, mime_type)},
        )
        if response.status_code != 200 or len(response.content) > 512 * 1024:
            raise HTTPException(502, "图片或语音识别暂时不可用，请补充文字说明。")
        try:
            return TypeAdapter(dict[str, JsonValue]).validate_json(
                response.content
            )
        except ValidationError as exc:
            raise HTTPException(502, "识别服务没有返回有效结果。") from exc

    async def classify_intent(
        self,
        *,
        project_id: str,
        provider_id: str,
        model: str,
        classification_input: Mapping[str, object],
    ) -> JsonObject:
        """Classify one customer message through tgo-ai's strict intent API."""
        response = await self._make_request(
            "POST",
            "/api/v1/analysis/intent",
            json_data={
                "provider_id": provider_id,
                "model": model,
                "classification_input": classification_input,
            },
            extra_headers={
                "X-Internal-API-Key": settings.SECRET_KEY,
                "X-Project-Id": project_id,
            },
        )
        result = await self._handle_response(response)
        if not isinstance(result, dict):
            raise HTTPException(
                status_code=502,
                detail="AI intent service returned an invalid response",
            )
        return result

    # ------------------------------------------------------------------
    # Skill management (proxy to tgo-ai /api/v1/skills)
    # ------------------------------------------------------------------

    def _skill_headers(self, project_id: str) -> Dict[str, str]:
        """Build extra headers for skill requests."""
        return {"X-Project-Id": project_id}

    async def import_skill(
        self, project_id: str, import_data: Mapping[str, object]
    ) -> JsonObject:
        """Import a skill from GitHub (proxy to tgo-ai)."""
        response = await self._make_request(
            "POST",
            "/api/v1/skills/import",
            json_data=import_data,
            extra_headers=self._skill_headers(project_id),
        )
        return await self._object_response(response)

    async def list_skills(self, project_id: str) -> list[JsonObject]:
        """List all skills visible to a project (private + official)."""
        response = await self._make_request(
            "GET",
            "/api/v1/skills",
            extra_headers=self._skill_headers(project_id),
        )
        return await self._object_list_response(response)

    async def create_skill(
        self, project_id: str, skill_data: Mapping[str, object]
    ) -> JsonObject:
        """Create a new project-private skill."""
        response = await self._make_request(
            "POST",
            "/api/v1/skills",
            json_data=skill_data,
            extra_headers=self._skill_headers(project_id),
        )
        return await self._object_response(response)

    async def create_humanization_skill(
        self, project_id: str, skill_data: Mapping[str, object]
    ) -> JsonObject:
        """Create a trainable humanization skill."""
        response = await self._make_request(
            "POST",
            "/api/v1/skills/humanization",
            json_data=skill_data,
            extra_headers=self._skill_headers(project_id),
        )
        result = await self._handle_response(response)
        if not isinstance(result, dict):
            raise HTTPException(
                status_code=502,
                detail="AI skill service returned an invalid response",
            )
        return result

    async def add_humanization_training_sample(
        self,
        project_id: str,
        skill_name: str,
        sample_data: Mapping[str, object],
    ) -> JsonObject:
        """Store one pending assist-mode correction."""
        response = await self._make_request(
            "POST",
            f"/api/v1/skills/{skill_name}/training-samples",
            json_data=sample_data,
            extra_headers=self._skill_headers(project_id),
        )
        result = await self._handle_response(response)
        if not isinstance(result, dict):
            raise HTTPException(
                status_code=502,
                detail="AI skill service returned an invalid response",
            )
        return result

    async def apply_humanization_training(
        self, project_id: str, skill_name: str, data: Mapping[str, object]
    ) -> JsonObject:
        """Publish all pending corrections for one humanization skill."""
        response = await self._make_request(
            "POST",
            f"/api/v1/skills/{skill_name}/apply-training",
            json_data=data,
            extra_headers=self._skill_headers(project_id),
        )
        result = await self._handle_response(response)
        if not isinstance(result, dict):
            raise HTTPException(
                status_code=502,
                detail="AI skill service returned an invalid response",
            )
        return result

    async def review_humanization_training(
        self, project_id: str, skill_name: str
    ) -> TrainingReview:
        response = await self._make_request(
            "GET",
            f"/api/v1/skills/{skill_name}/training-review",
            extra_headers=self._skill_headers(project_id),
        )
        return TrainingReview.model_validate(
            await self._handle_response(response)
        )

    async def match_humanization_context(
        self,
        project_id: str,
        skill_name: str,
        customer_message: str,
        candidate: TrainingPublishRequest | None = None,
        factual_draft: str = "",
        recent_messages: list[ConversationTurn] | None = None,
    ) -> HumanizationContext:
        payload = HumanizationMatchRequest(
            customer_message=customer_message,
            factual_draft=factual_draft,
            recent_messages=recent_messages or [],
            candidate=candidate,
        ).model_dump()
        response = await self._make_request(
            "POST",
            f"/api/v1/skills/{skill_name}/match-context",
            json_data=payload,
            extra_headers=self._skill_headers(project_id),
        )
        return HumanizationContext.model_validate(
            await self._handle_response(response)
        )

    async def get_skill(self, project_id: str, skill_name: str) -> JsonObject:
        """Get skill details."""
        response = await self._make_request(
            "GET",
            f"/api/v1/skills/{skill_name}",
            extra_headers=self._skill_headers(project_id),
        )
        return await self._object_response(response)

    async def update_skill(
        self,
        project_id: str,
        skill_name: str,
        skill_data: Mapping[str, object],
    ) -> JsonObject:
        """Update a project-private skill."""
        response = await self._make_request(
            "PATCH",
            f"/api/v1/skills/{skill_name}",
            json_data=skill_data,
            extra_headers=self._skill_headers(project_id),
        )
        return await self._object_response(response)

    async def toggle_skill(
        self,
        project_id: str,
        skill_name: str,
        toggle_data: Mapping[str, object],
    ) -> JsonObject:
        """Toggle a skill's enabled/disabled state."""
        response = await self._make_request(
            "PUT",
            f"/api/v1/skills/{skill_name}/toggle",
            json_data=toggle_data,
            extra_headers=self._skill_headers(project_id),
        )
        return await self._object_response(response)

    async def delete_skill(self, project_id: str, skill_name: str) -> None:
        """Delete a project-private skill."""
        response = await self._make_request(
            "DELETE",
            f"/api/v1/skills/{skill_name}",
            extra_headers=self._skill_headers(project_id),
        )
        if response.status_code not in (200, 204):
            await self._handle_response(response)

    async def get_skill_file(
        self, project_id: str, skill_name: str, file_path: str
    ) -> str:
        """Read a sub-file from a skill."""
        response = await self._make_request(
            "GET",
            f"/api/v1/skills/{skill_name}/files/{file_path}",
            extra_headers=self._skill_headers(project_id),
        )
        if response.status_code != 200:
            await self._handle_response(response)
        return response.text

    async def put_skill_file(
        self,
        project_id: str,
        skill_name: str,
        file_path: str,
        file_content: str,
    ) -> None:
        """Create or update a sub-file inside a skill."""
        headers = self._skill_headers(project_id)
        headers["Content-Type"] = "text/plain; charset=utf-8"
        response = await self._make_request(
            "PUT",
            f"/api/v1/skills/{skill_name}/files/{file_path}",
            content=file_content,
            extra_headers=headers,
        )
        if response.status_code not in (200, 201):
            await self._handle_response(response)

    async def delete_skill_file(
        self, project_id: str, skill_name: str, file_path: str
    ) -> None:
        """Delete a sub-file from a skill."""
        response = await self._make_request(
            "DELETE",
            f"/api/v1/skills/{skill_name}/files/{file_path}",
            extra_headers=self._skill_headers(project_id),
        )
        if response.status_code not in (200, 204):
            await self._handle_response(response)

    async def agent_activation_policies(
        self, project_id: UUID, staff_id: UUID, agent_id: UUID
    ) -> AgentActivationPolicies:
        from app.services.agent_activation_policy_client import read

        return await read(self, project_id, staff_id, agent_id)

    async def submit_agent_activation_request(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
        command: AgentActivationRequest,
    ) -> AgentActivationReceipt:
        from app.services.agent_activation_request_client import submit

        return await submit(self, project_id, staff_id, agent_id, command)

    async def lookup_agent_activation_request(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
        request_key: UUID,
    ) -> AgentActivationReceipt:
        from app.services.agent_activation_request_client import lookup

        return await lookup(self, project_id, staff_id, agent_id, request_key)

    async def agent_device_script_binding(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
    ) -> AgentDeviceScriptState:
        from app.services.agent_device_script_binding_client import read

        return await read(self, project_id, staff_id, agent_id)

    async def save_agent_device_script_binding(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
        command: AgentDeviceScriptWrite,
    ) -> AgentDeviceScriptRecord:
        from app.services.agent_device_script_binding_client import save

        return await save(self, project_id, staff_id, agent_id, command)

    async def agent_device_script_binding_request(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
        request_id: UUID,
    ) -> AgentDeviceScriptRecord:
        from app.services.agent_device_script_binding_client import recover

        return await recover(self, project_id, staff_id, agent_id, request_id)

    async def start_agent_device_script_trial(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
        command: ScriptTrialStart,
    ) -> ScriptTrialReport:
        from app.services.agent_device_script_trial_client import start

        return await start(self, project_id, staff_id, agent_id, command)

    async def agent_device_script_request_state(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
        request_id: UUID,
    ) -> ScriptTrialRequestState:
        from app.services.agent_device_script_trial_client import request_state

        return await request_state(
            self, project_id, staff_id, agent_id, request_id
        )

    async def cancel_agent_device_script_request(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
        command: ScriptTrialControl,
    ) -> ScriptTrialRequestState:
        from app.services.agent_device_script_trial_client import (
            cancel_request,
        )

        return await cancel_request(
            self, project_id, staff_id, agent_id, command
        )

    async def get_agent_device_script_trial(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
        report_id: UUID,
    ) -> ScriptTrialReport:
        from app.services.agent_device_script_trial_client import read

        return await read(self, project_id, staff_id, agent_id, report_id)

    async def agent_device_script_trial_request(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
        request_id: UUID,
    ) -> ScriptTrialReport:
        from app.services.agent_device_script_trial_client import recover

        return await recover(self, project_id, staff_id, agent_id, request_id)

    async def list_agent_device_script_cases(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        *,
        skip: int = 0,
        limit: int = 20,
    ) -> ScriptCasePage:
        from app.services.agent_device_script_trial_client import list_cases

        return await list_cases(
            self, project, actor, agent, skip=skip, limit=limit
        )

    async def save_agent_device_script_case(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        command: ScriptCaseWrite,
    ) -> ScriptCaseRecord:
        from app.services.agent_device_script_trial_client import save_case

        return await save_case(self, project, actor, agent, command)

    async def get_agent_device_script_case(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        version: UUID,
    ) -> ScriptCaseRead:
        from app.services.agent_device_script_trial_client import read_case

        return await read_case(self, project, actor, agent, version)

    async def agent_device_script_case_request(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        request: UUID,
    ) -> ScriptCaseRecord:
        from app.services.agent_device_script_trial_client import recover_case

        return await recover_case(self, project, actor, agent, request)

    async def agent_device_script_case_save_state(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        request: UUID,
    ) -> ScriptCaseSaveState:
        from app.services.agent_device_script_trial_client import (
            case_save_state,
        )

        return await case_save_state(self, project, actor, agent, request)

    async def cancel_agent_device_script_case_save(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        command: ScriptCaseSaveControl,
    ) -> ScriptCaseSaveState:
        from app.services.agent_device_script_trial_client import (
            case_save_state,
        )

        return await case_save_state(
            self, project, actor, agent, command.request_id, command=command
        )

    async def run_agent_device_script_case(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        version: UUID,
        command: ScriptTrialControl,
    ) -> ScriptCaseTrialResult:
        from app.services.agent_device_script_trial_client import run_case

        return await run_case(self, project, actor, agent, version, command)

    async def agent_device_script_evidence(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        report: UUID,
    ) -> ScriptTrialEvidence:
        from app.services.agent_device_script_trial_client import read_evidence

        return await read_evidence(self, project, actor, agent, report)

    async def agent_device_script_review(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        report: UUID,
        *,
        command: ScriptReviewWrite | None = None,
        revoke: UUID | None = None,
    ) -> ScriptReviewState:
        from app.services.agent_device_script_trial_client import script_review

        return await script_review(
            self, project, actor, agent, report, command=command, revoke=revoke
        )

    async def agent_device_script_run_check(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        report: UUID,
    ) -> ScriptRunCheck:
        from app.services.agent_device_script_trial_client import (
            script_run_check,
        )

        return await script_run_check(self, project, actor, agent, report)

    async def agent_device_script_selection(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
    ) -> ScriptSelectionState:
        from app.services.agent_device_script_selection_client import (
            read_selection,
        )

        return await read_selection(self, project, actor, agent)

    async def save_agent_device_script_selection(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        command: ScriptSelectionWrite,
    ) -> ScriptSelectionRecord:
        from app.services.agent_device_script_selection_client import (
            save_selection,
        )

        return await save_selection(self, project, actor, agent, command)

    async def agent_device_script_selection_request(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        request: UUID,
    ) -> ScriptSelectionRecord:
        from app.services.agent_device_script_selection_client import (
            selection_request,
        )

        return await selection_request(self, project, actor, agent, request)

    async def start_agent_device_script_run(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        command: ScriptRunStart,
    ) -> ScriptRunView:
        from app.services.agent_device_script_run_client import run_call

        return await run_call(
            self,
            project,
            actor,
            agent,
            command.request_id,
            "start",
            command,
        )

    async def agent_device_script_run(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        request: UUID,
    ) -> ScriptRunView:
        from app.services.agent_device_script_run_client import run_call

        return await run_call(self, project, actor, agent, request, "read")

    async def cancel_agent_device_script_run(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        command: ScriptRunControl,
    ) -> ScriptRunView:
        from app.services.agent_device_script_run_client import run_call

        return await run_call(
            self,
            project,
            actor,
            agent,
            command.request_id,
            "cancel",
            command,
        )

    async def agent_business_connections(
        self, project_id: UUID, staff_id: UUID, agent_id: UUID
    ) -> AgentBusinessConnections:
        from app.services.agent_business_connection_client import read

        return await read(self, project_id, staff_id, agent_id)

    async def save_agent_business_connection(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
        connection_id: UUID,
        command: AgentBusinessConnectionWrite,
    ) -> AgentBusinessConnectionRecord:
        from app.services.agent_business_connection_client import save

        return await save(
            self, project_id, staff_id, agent_id, connection_id, command
        )

    async def agent_business_connection_request(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
        request_id: UUID,
    ) -> AgentBusinessConnectionRecord:
        from app.services.agent_business_connection_client import recover

        return await recover(self, project_id, staff_id, agent_id, request_id)

    async def verify_agent_execution(
        self, reference: AgentExecutionReference
    ) -> AgentExecutionCheckReceipt:
        from app.services.agent_execution_client import verify

        return await verify(self, reference)

    async def capture_agent_execution_reference(
        self,
        project_id: str,
        agent_id: str,
        channel: str | None,
    ) -> AgentExecutionReference:
        response = await self._make_request(
            "GET",
            f"/api/v1/agents/execution-reference/{UUID(agent_id)}",
            extra_headers=self._execution_headers(project_id, channel),
        )
        return AgentExecutionReference.model_validate(
            await self._handle_response(response)
        )

    async def agent_activation_readiness(
        self, project_id: UUID, staff_id: UUID, agent_id: UUID
    ) -> AgentActivationReadiness:
        from app.services.agent_activation_policy_client import readiness

        return await readiness(self, project_id, staff_id, agent_id)

    async def save_agent_activation_policy(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
        channel: KnowledgeChannel,
        command: AgentActivationPolicyChange,
    ) -> AgentActivationPolicyRecord:
        from app.services.agent_activation_policy_client import save

        return await save(
            self, project_id, staff_id, agent_id, channel, command
        )

    async def remove_agent_activation_policy(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
        channel: KnowledgeChannel,
        revision: int,
    ) -> AgentActivationPolicyRecord:
        from app.services.agent_activation_policy_client import remove

        return await remove(
            self, project_id, staff_id, agent_id, channel, revision
        )

    async def save_agent_draft(
        self,
        project_id: UUID,
        staff_id: UUID,
        draft_id: UUID,
        request: "ScenarioAgentSnapshot",
    ) -> "ScenarioAgentReceipt":
        response = await self._make_request(
            "POST",
            f"/api/v1/agent-drafts/{draft_id}",
            json_data=request.model_dump(mode="json"),
            extra_headers=self._trial_headers(project_id, staff_id),
        )
        receipt = self._draft_receipt(
            response, project_id, staff_id, draft_id, request.revision
        )
        if receipt.activation_selection != request.activation_selection:
            raise HTTPException(502, "员工岗位配置结果与当前草稿不一致")
        return receipt

    async def get_agent_draft(
        self,
        project_id: UUID,
        staff_id: UUID,
        draft_id: UUID,
    ) -> "ScenarioAgentReceipt":
        response = await self._make_request(
            "GET",
            f"/api/v1/agent-drafts/{draft_id}",
            extra_headers=self._trial_headers(project_id, staff_id),
        )
        return self._draft_receipt(response, project_id, staff_id, draft_id)

    def _draft_receipt(
        self,
        response: httpx.Response,
        project_id: UUID,
        staff_id: UUID,
        draft_id: UUID,
        revision: int | None = None,
    ) -> "ScenarioAgentReceipt":
        if not response.is_success:
            raise HTTPException(response.status_code, "员工配置尚未保存，请恢复原草稿后重试")
        try:
            receipt = ScenarioAgentReceipt.model_validate_json(
                response.content
            )
        except ValidationError:
            raise HTTPException(502, "员工配置服务返回了无效结果") from None
        if (
            receipt.project_id != project_id
            or receipt.created_by != staff_id
            or receipt.draft_id != draft_id
            or revision is not None
            and receipt.revision != revision
        ):
            raise HTTPException(502, "员工配置结果与当前草稿不一致")
        return receipt

    def _trial_headers(
        self, project_id: UUID, staff_id: UUID
    ) -> dict[str, str]:
        return {
            "X-Internal-API-Key": settings.SECRET_KEY,
            "X-Project-Id": str(project_id),
            "X-Staff-Id": str(staff_id),
        }

    async def review_agent_trial(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
        report_id: UUID,
        command: KnowledgeReviewCommand,
    ) -> KnowledgeReviewResponse:
        from app.services.agent_trial_review_client import submit

        return await submit(
            self, project_id, staff_id, agent_id, report_id, command
        )

    async def agent_trial_review_state(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
        report_id: UUID,
        query: KnowledgeReviewQuery,
    ) -> KnowledgeReviewState:
        from app.services.agent_trial_review_client import state

        return await state(
            self, project_id, staff_id, agent_id, report_id, query
        )

    async def agent_knowledge_capabilities(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
        query: KnowledgeCapabilityQuery,
    ) -> AgentKnowledgeCapabilities:
        from app.services.agent_trial_review_client import capabilities

        return await capabilities(self, project_id, staff_id, agent_id, query)

    def _trial_report(
        self,
        response: httpx.Response,
        agent_id: UUID,
        *,
        request_key: UUID | None = None,
        report_id: UUID | None = None,
    ) -> AgentTrialReport:
        if not response.is_success:
            # Do not log or echo upstream validation input/provider errors.
            raise HTTPException(response.status_code, "试聊请求未完成，请查看报告或检查配置")
        try:
            report = AgentTrialReport.model_validate_json(response.content)
        except ValidationError:
            raise HTTPException(502, "试聊服务返回了无效报告") from None
        if (
            report.agent_id != agent_id
            or request_key is not None
            and report.request_key != request_key
            or report_id is not None
            and report.id != report_id
        ):
            raise HTTPException(502, "试聊服务返回的报告标识不一致")
        return report

    async def start_agent_trial(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
        request: AgentTrialRequest,
    ) -> AgentTrialReport:
        response = await self._make_request(
            "POST",
            f"/api/v1/agents/{agent_id}/trials",
            json_data=request.model_dump(mode="json"),
            extra_headers=self._trial_headers(project_id, staff_id),
            timeout=max(self.timeout, request.timeout + 15),
        )
        return self._trial_report(
            response, agent_id, request_key=request.request_key
        )

    async def match_agent_trial_request(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
        request: AgentTrialRequest,
    ) -> AgentTrialReport:
        response = await self._make_request(
            "POST",
            f"/api/v1/agents/{agent_id}/trials/lookup",
            json_data=request.model_dump(mode="json"),
            extra_headers=self._trial_headers(project_id, staff_id),
        )
        return self._trial_report(
            response, agent_id, request_key=request.request_key
        )

    async def agent_trial_by_request(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
        request_key: UUID,
    ) -> AgentTrialReport:
        response = await self._make_request(
            "GET",
            f"/api/v1/agents/{agent_id}/trials/requests/{request_key}",
            extra_headers=self._trial_headers(project_id, staff_id),
        )
        return self._trial_report(response, agent_id, request_key=request_key)

    async def get_agent_trial(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
        report_id: UUID,
    ) -> AgentTrialReport:
        response = await self._make_request(
            "GET",
            f"/api/v1/agents/{agent_id}/trials/{report_id}",
            extra_headers=self._trial_headers(project_id, staff_id),
        )
        return self._trial_report(response, agent_id, report_id=report_id)

    async def cancel_agent_trial(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
        report_id: UUID,
    ) -> AgentTrialReport:
        response = await self._make_request(
            "POST",
            f"/api/v1/agents/{agent_id}/trials/{report_id}/cancel",
            extra_headers=self._trial_headers(project_id, staff_id),
        )
        return self._trial_report(response, agent_id, report_id=report_id)

    async def list_agent_trials(
        self,
        project_id: UUID,
        staff_id: UUID,
        agent_id: UUID,
        limit: int = 20,
    ) -> list[AgentTrialReport]:
        response = await self._make_request(
            "GET",
            f"/api/v1/agents/{agent_id}/trials",
            params={"limit": limit},
            extra_headers=self._trial_headers(project_id, staff_id),
        )
        if not response.is_success:
            raise HTTPException(response.status_code, "无法读取试聊报告")
        try:
            reports = TypeAdapter(list[AgentTrialReport]).validate_json(
                response.content
            )
        except ValidationError:
            raise HTTPException(502, "试聊服务返回了无效报告列表") from None
        if len(reports) > limit or any(
            report.agent_id != agent_id for report in reports
        ):
            raise HTTPException(502, "试聊报告列表与请求不一致")
        return reports


# Global AI client instance
ai_client = AIServiceClient()
