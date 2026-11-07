"""Build direct single-agent runtime instances."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from agno.agent import Agent, RemoteAgent

from app.config import settings
from app.core.logging import get_logger
from app.models.internal import AgentExecutionContext
from app.runtime.tools.builder.agent_builder import AgentBuilder
from app.runtime.tools.store_remote_agent import StoreRemoteAgent
from app.runtime.tools.config import ToolsRuntimeSettings
from app.runtime.tools.models import (
    AgentConfig,
    AgentRunRequest,
    MCPConfig,
    RagConfig,
    WorkflowConfig,
)
from app.services.current_knowledge import (
    evidence_prompt,
    retrieve_current_knowledge,
)
from app.services.agent_execution_scope import current_execution_scope
from .business_tools import build_business_tools
from .device_script_tools import build_device_script_tools


@dataclass
class BuiltAgent:
    """Container holding the constructed runnable agent."""

    agent: Agent | RemoteAgent


class AgnoAgentBuilder:
    (
        'Build a single runnable Agno agent from the resolved execution '
        'context.'
    )

    def __init__(
        self, settings_obj: Optional[ToolsRuntimeSettings] = None
    ) -> None:
        runtime_settings = settings_obj or settings.tools_runtime
        self._agent_builder = AgentBuilder(runtime_settings)
        self._logger = get_logger("runtime.supervisor.agents.builder")

    async def build_agent(self, context: AgentExecutionContext) -> BuiltAgent:
        """Build the direct agent for one execution request."""
        if (
            context.require_current_knowledge
            and context.response_purpose != "expression"
        ):
            context.knowledge_evidence = (
                context.knowledge_evidence
                or await retrieve_current_knowledge(context)
            )
            context.system_message = (
                (context.system_message or "")
                + "\n"
                + evidence_prompt(context.knowledge_evidence)
            )
        if context.response_purpose == "expression":
            context = context.model_copy(
                update={
                    "disable_tools": True,
                    "enable_memory": False,
                    "session_id": None,
                    "user_id": None,
                    "markdown": False,
                    "ui_mode": "text",
                    "expected_output": None,
                }
            )
        request = AgentRunRequest(
            message=context.message,
            config=self._build_agent_config(context),
            session_id=context.session_id,
            user_id=context.user_id,
            project_id=context.project_id,
            agent_id=str(context.agent.id),
            request_id=context.request_id,
            parent_workflow_execution_id=context.parent_workflow_execution_id,
            workflow_lineage=context.workflow_lineage,
            business_principal=context.business_principal,
            business_context_token=context.business_context_token,
            skills_enabled=context.agent.skills_enabled,
            enable_memory=context.enable_memory,
            disable_tools=context.disable_tools,
            excluded_tool_ids=context.excluded_tool_ids,
        )
        agno_agent = await self._agent_builder.build_agent(
            request, internal_agent=context.agent
        )
        if isinstance(agno_agent, Agent):
            business_tools = [
                *build_business_tools(context),
                *await build_device_script_tools(context),
            ]
            if business_tools:
                agno_agent.tools = [*(agno_agent.tools or []), *business_tools]
                agno_agent.debug_mode = False
        scope = current_execution_scope()
        if scope is not None and isinstance(agno_agent, Agent):
            agno_agent.tool_hooks = [
                scope.tool_hook,
                *(agno_agent.tool_hooks or []),
            ]
        if context.require_current_knowledge and isinstance(agno_agent, Agent):
            # Do not put documents or business receipts in debug logs.

            agno_agent.debug_mode = False
        # The SDK RemoteAgent exposes read-only identity properties. Keep the
        # remote endpoint identity intact and bind local event identity.

        identity: dict[str, object] = {
            "agent_id": str(context.agent.id),
            "project_id": context.project_id,
            "request_id": context.request_id,
        }
        if isinstance(agno_agent, RemoteAgent):
            if not isinstance(agno_agent, StoreRemoteAgent):
                raise ValueError("远程员工不支持本地身份绑定")
            agno_agent.bind_runtime_identity(
                str(context.agent.id), context.agent.name, identity
            )
        else:
            agno_agent.id = str(context.agent.id)
            agno_agent.name = context.agent.name or getattr(
                agno_agent, "name", "Agent"
            )
            agno_agent.metadata = {**(context.agent.config or {}), **identity}
        return BuiltAgent(agent=agno_agent)

    def _build_agent_config(
        self, context: AgentExecutionContext
    ) -> AgentConfig:
        """Build the effective AgentConfig for a single runtime execution."""
        config: Dict[str, Any] = dict(context.agent.config or {})

        mcp_config = None
        if context.mcp_url and context.agent.tools:
            mcp_config = MCPConfig(
                url=context.mcp_url,
                tools=[
                    tool.tool_name
                    for tool in context.agent.tools
                    if tool.enabled
                ],
                auth_required=False,
            )
        rag_config = None
        if (
            context.rag_url
            and context.agent.collections
            and not context.require_current_knowledge
        ):
            rag_config = RagConfig(
                rag_url=context.rag_url,
                collections=[
                    binding.collection_id
                    for binding in context.agent.collections
                    if binding.enabled
                ],
                project_id=context.project_id,
                knowledge_channel=context.knowledge_channel,
            )
        workflow_config = None
        workflow_url = getattr(settings, "workflow_service_url", None)
        if workflow_url and context.agent.workflows:
            workflow_config = WorkflowConfig(
                workflow_url=workflow_url,
                agent_id=str(context.agent.id),
                workflows=[
                    str(binding.workflow_id)
                    for binding in context.agent.workflows
                    if binding.enabled
                ],
                release_versions={
                    str(binding.workflow_id): binding.release_version
                    for binding in context.agent.workflows
                    if binding.enabled and binding.release_version is not None
                },
                release_ids={
                    str(binding.workflow_id): binding.release_id
                    for binding in context.agent.workflows
                    if binding.enabled and binding.release_id is not None
                },
                release_digests={
                    str(binding.workflow_id): binding.release_digest
                    for binding in context.agent.workflows
                    if binding.enabled and binding.release_digest is not None
                },
                project_id=context.project_id,
            )
        if context.response_purpose == "expression":
            return AgentConfig(
                model_name=context.agent.model,
                provider_credentials=context.agent.llm_provider_credentials,
                system_prompt=context.system_message,
                temperature=context.temperature
                if context.temperature is not None
                else 0.2,
                enable_memory=False,
                markdown=False,
                ui_mode="text",
                expression_only=True,
            )
        return AgentConfig(
            model_name=context.agent.model,
            temperature=(
                context.temperature
                if context.temperature is not None
                else config.get("temperature")
            ),
            max_tokens=config.get("max_tokens"),
            system_prompt=context.agent.instruction,
            system_message=context.system_message,
            expected_output=context.expected_output
            or config.get("expected_output"),
            mcp_config=mcp_config,
            rag=rag_config,
            workflow=workflow_config,
            enable_memory=context.enable_memory,
            provider_credentials=context.agent.llm_provider_credentials,
            markdown=(
                context.markdown
                if context.markdown is not None
                else config.get("markdown")
            ),
            add_datetime_to_context=config.get("add_datetime_to_context"),
            add_location_to_context=config.get("add_location_to_context"),
            timezone_identifier=config.get("timezone_identifier"),
            tool_call_limit=config.get("tool_call_limit"),
            num_history_runs=config.get("num_history_runs"),
            ui_mode=context.ui_mode,
        )
