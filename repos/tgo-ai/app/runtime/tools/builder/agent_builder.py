"""Agent builder with RAG and MCP integration."""

from __future__ import annotations

import traceback
import types
from math import ceil
from typing import TYPE_CHECKING, Any, Dict, List, Optional, TypedDict, Union
from uuid import UUID

import httpx
from agno.agent import Agent, RemoteAgent
from agno.db.postgres import PostgresDb
from agno.memory import MemoryManager
from agno.models.anthropic import Claude
from agno.models.google import Gemini
from agno.models.openai import OpenAIChat
from agno.models.base import Model
from google.genai.types import HttpOptions
from agno.tools.function import Function
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import SecretStr

from app.config import settings
from app.exceptions import ExternalServiceError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.json_render import JsonRenderSchemaManager
from app.models.internal import Agent as InternalAgent
from app.models.internal import AgentTool
from app.runtime.core.exceptions import (
    InvalidConfigurationError,
    MCPAuthenticationError,
    MCPConnectionError,
    MCPToolError,
    MissingConfigurationError,
)
from app.runtime.tools.config import ToolsRuntimeSettings
from app.runtime.tools.models import (
    AgentConfig,
    AgentRunRequest,
    MCPConfig,
    RagConfig,
    WorkflowConfig,
)
from app.runtime.tools.store_remote_agent import (
    StoreRemoteAgent as StoreRemoteAgent,
)
from app.runtime.tools.token import get_mcp_access_token
from app.runtime.tools.utils import (
    create_agno_mcp_tool,
    create_http_tool,
    create_plugin_tool,
    create_rag_tool,
    create_workflow_tools,
    wrap_mcp_authenticate_tool,
)
from app.services.api_service import api_service_client
from app.services.device_control_client import device_control_client
from app.services.device_execution import device_headers_factory
from app.schemas.mcp_connection import mcp_connection_headers
from app.schemas.business_execution_context import BusinessRequestPrincipal
from app.runtime.tools.mcp_bindings import group_mcp_bindings
from app.runtime.tools.saved_mcp import SavedMCPTools

_logger = get_logger(__name__)


UNEDITABLE_SYSTEM_PROMPT = (
    '\n'
    'If the tool throws an error requiring authentication, provide the '
    'user with a Markdown link to the authentication page and prompt '
    'them to authenticate.\n'
    'For factual answers based on bound knowledge, call each RAG search '
    'tool at most once per user turn. Treat the first governed result as '
    'complete. Use only explicit facts from that result. Never claim '
    'that an item matches a requested price, policy, date, '
    'specification, or other constraint unless the matching value is '
    'explicitly present. If no result satisfies the constraint, clearly '
    'say that no confirmed match was found.\n'
    'When a tool is needed, call it immediately without any user-facing '
    'preamble. Never announce that you are about to search, check, '
    'think, or use a tool.\n'
    'Customer context management applies identically to every channel. '
    'When the customer explicitly provides a stable profile fact such as '
    'their name, contact method, company, position, or address, call '
    'update_user_info once with only the stated fields. When the '
    'customer clearly expresses satisfaction, dissatisfaction, emotion, '
    'or a service intent, call update_user_sentiment once. Use '
    'add_user_tags only for its documented controlled vocabulary and '
    'only when evidence is explicit. Use update_user_memory only for '
    'stable preferences, durable constraints, or decisions that will '
    'help in future conversations. Never store passwords, verification '
    'codes, authentication tokens, payment credentials, government '
    'identifiers, medical details, or unconfirmed inferences in profile '
    'fields, tags, sentiment, or long-term memory. Do not mention these '
    'background writes to the customer unless the write fails and '
    'affects the requested service.'
)

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant that has access to a variety of tools."
)


if TYPE_CHECKING:
    from agno.skills.loaders import SkillLoader


class _CustomToolArguments(TypedDict):
    agent_id: str
    session_id: str | None
    user_id: str | None
    project_id: str | None
    request_id: str | None


class AgentBuilder:
    """Constructs Agno agents with optional RAG and MCP tooling."""

    def __init__(self, settings: ToolsRuntimeSettings) -> None:
        self._settings = settings
        self._logger = get_logger("runtime.tools.AgentBuilder")
        self._memory_db: Optional[PostgresDb] = None

    @staticmethod
    def _is_cancellation_like_error(exc: BaseException) -> bool:
        (
            'Detect anyio/asyncio cancellation-style exceptions emitted by '
            'MCP clients.'
        )
        name = type(exc).__name__.lower()
        message = str(exc).lower()
        return "cancel" in name or "cancel scope" in message

    # ------------------------------------------------------------------
    # Public API
    async def build_agent(
        self,
        request: AgentRunRequest,
        internal_agent: Optional["InternalAgent"] = None,
    ) -> Union[Agent, RemoteAgent]:
        (
            'Build an agent configured for the given request.\n'
            '\n'
            '        Args:\n'
            '            request: The agent run request containing '
            'configuration\n'
            '            internal_agent: Optional internal agent model '
            'containing tool bindings\n'
            '        '
        )
        # # 1. Handle Remote Store Agents
        # if internal_agent and getattr(internal_agent,
        # "is_remote_store_agent", False):
        # return await self._build_remote_store_agent(request, internal_agent)

        # 2. Handle Local Agents
        return await self._build_local_agent(request, internal_agent)

    async def _build_remote_store_agent(
        self,
        request: AgentRunRequest,
        internal_agent: "InternalAgent",
    ) -> StoreRemoteAgent:
        """Helper to construct a StoreRemoteAgent."""
        remote_url = internal_agent.remote_agent_url
        remote_identifier = internal_agent.store_agent_id
        if not remote_url or not remote_identifier:
            raise InvalidConfigurationError("远程员工缺少服务地址或员工编号")
        # 获取商店 API Key
        api_key = None
        if request.project_id:
            try:
                credential = await api_service_client.get_store_credential(
                    request.project_id
                )
                if credential:
                    api_key = credential.get("api_key")
            except Exception as e:
                self._logger.warning(
                    "Failed to fetch store credential", error=str(e)
                )

        if not api_key:
            api_key = settings.store_api_key

        # 为远程 Agent 加载本地工具
        local_tools = []
        if internal_agent.tools:
            try:
                local_tools = await self._build_mcp_tools_from_agent(
                    internal_agent,
                    request.session_id,
                    request.user_id,
                    project_id=request.project_id,
                )
            except Exception as e:
                self._logger.warning(
                    "Failed to build local tools for remote agent",
                    error=str(e),
                )

        self._logger.debug(
            "Creating RemoteAgent",
            agent_id=internal_agent.store_agent_id,
            base_url=internal_agent.remote_agent_url,
        )
        return StoreRemoteAgent(
            base_url=remote_url,
            agent_id=remote_identifier,
            timeout=60.0,
            override_id=str(internal_agent.id),
            override_name=internal_agent.name,
            api_key=api_key,
            tools=local_tools,
        )

    async def _build_local_agent(
        self,
        request: AgentRunRequest,
        internal_agent: Optional["InternalAgent"] = None,
    ) -> Agent:
        """Helper to construct a local Agno Agent."""
        config = self._normalize_config(request.config)
        from app.services.commercial_ai_limits import constrain_agent

        config = constrain_agent(config, request.message)
        tools: List[Any] = []
        if not request.disable_tools:
            tools = await self._build_tools(
                config,
                request.session_id,
                request.user_id,
                internal_agent=internal_agent,
                project_id=request.project_id,
                agent_id=request.agent_id,
                request_id=request.request_id,
                business_principal=request.business_principal,
                business_context_token=request.business_context_token,
                parent_workflow_execution_id=(
                    request.parent_workflow_execution_id
                ),
                workflow_lineage=request.workflow_lineage,
                excluded_tool_ids=request.excluded_tool_ids,
            )

        # Build Agno Skills object if skills_enabled
        skills_obj = None
        skills_enabled = (
            request.skills_enabled
            if request.skills_enabled is not None
            else True
        )
        if not request.disable_tools and skills_enabled and request.project_id:
            try:
                skills_obj = self._build_skills(request.project_id)
                if skills_obj:
                    self._logger.debug(
                        "Skills object built for agent",
                        project_id=request.project_id,
                    )
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    "Skill loading failed, continuing without skills",
                    project_id=request.project_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

        model = self._initialize_model(config)
        instructions = (
            config.system_prompt
            if config.expression_only
            else self._compose_system_prompt(
                config.system_prompt, ui_mode=config.ui_mode
            )
        )
        enable_memory = (
            bool(config.enable_memory)
            if request.enable_memory is None
            else request.enable_memory
        )

        self._logger.debug(
            "Creating local agent",
            tool_count=len(tools),
            model_name=config.model_name,
        )

        try:
            agent_kwargs: Dict[str, Any] = {
                "model": model,
                "tools": tools,
                "instructions": instructions,
                "additional_context": config.system_message,
                "expected_output": config.expected_output,
                "description": "Tools agent with MCP and RAG support",
                "markdown": config.markdown
                if config.markdown is not None
                else True,
                "add_datetime_to_context": config.add_datetime_to_context
                if config.add_datetime_to_context is not None
                else True,
                "add_location_to_context": config.add_location_to_context
                if config.add_location_to_context is not None
                else False,
                "timezone_identifier": config.timezone_identifier,
                "tool_call_limit": config.tool_call_limit,
                # Agno skips RunContent events by default when stream_events is
                # enabled. The supervisor needs both content and tool events so
                # an empty RunCompleted payload cannot erase a valid answer.
                "events_to_skip": [],
                "telemetry": False,
                "debug_mode": True,
                "debug_level": 2,
            }

            # Inject Agno Skills object (auto system-prompt + tool
            # registration)
            if skills_obj is not None:
                agent_kwargs["skills"] = skills_obj

            if request.session_id:
                agent_kwargs["session_id"] = request.session_id
            if request.user_id:
                agent_kwargs["user_id"] = request.user_id

            if enable_memory:
                memory_manager, memory_db = self._ensure_memory_backend(model)
                agent_kwargs.update(
                    db=memory_db,
                    memory_manager=memory_manager,
                    # Memory writes also expose tools / make model calls. A
                    # tool-free confirmation may read history, but not learn.
                    enable_agentic_memory=not request.disable_tools,
                    enable_user_memories=not request.disable_tools,
                    add_memories_to_context=True,
                    add_history_to_context=True,
                    num_history_runs=config.num_history_runs
                    if config.num_history_runs is not None
                    else 5,
                )

            return Agent(**agent_kwargs)
        except Exception as exc:
            raise InvalidConfigurationError(
                "Failed to create Agent instance",
                tools_count=len(tools),
                error=str(exc),
            ) from exc

    # ------------------------------------------------------------------
    # Configuration helpers
    def _normalize_config(self, config: Optional[AgentConfig]) -> AgentConfig:
        """Apply runtime defaults to the incoming configuration."""
        merged = (config or AgentConfig()).model_copy(deep=True)
        base = self._settings.model

        # Map base defaults if not provided in merged config
        merged.model_name = merged.model_name or base.name
        merged.temperature = (
            merged.temperature
            if merged.temperature is not None
            else base.temperature
        )
        merged.max_tokens = (
            merged.max_tokens
            if merged.max_tokens is not None
            else base.max_tokens
        )
        merged.system_prompt = merged.system_prompt or base.system_prompt

        return merged

    def _compose_system_prompt(
        self,
        configured_prompt: Optional[str],
        *,
        ui_mode: Optional[str] = None,
    ) -> str:
        """Compose the final system prompt for the selected UI protocol.

        Args:
            configured_prompt: The base system prompt from configuration.

        Returns:
            Composed system prompt string.
        """
        prompt = configured_prompt or DEFAULT_SYSTEM_PROMPT

        if ui_mode == "json_render":
            try:
                json_render_mgr = JsonRenderSchemaManager()
                json_render_block = json_render_mgr.generate_system_prompt(
                    include_schema=True,
                    include_examples=True,
                )
                prompt = f"{prompt}\n\n{json_render_block}"
                self._logger.debug(
                    "json-render schema injected into system prompt"
                )
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    "Failed to inject json-render schema into system prompt",
                    error=str(exc),
                )

        return f"{prompt}{UNEDITABLE_SYSTEM_PROMPT}"

    def resolve_model_instance(
        self, config: Optional[AgentConfig] = None
    ) -> Any:
        """Public helper to obtain a model instance using runtime defaults."""

        normalized = self._normalize_config(config)
        return self._initialize_model(normalized)

    # ------------------------------------------------------------------
    # Tool preparation
    async def _build_tools(
        self,
        config: AgentConfig,
        session_id: Optional[str],
        user_id: Optional[str],
        internal_agent: Optional["InternalAgent"] = None,
        project_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        request_id: Optional[str] = None,
        business_principal: BusinessRequestPrincipal | None = None,
        business_context_token: SecretStr | None = None,
        excluded_tool_ids: tuple[UUID, ...] = (),
        parent_workflow_execution_id: UUID | None = None,
        workflow_lineage: tuple[str, ...] = (),
    ) -> List[Any]:
        tools: List[Any] = []

        try:
            tools.extend(
                await self._build_rag_tools(
                    config.rag,
                    agent_id=agent_id,
                    session_id=session_id,
                    user_id=user_id,
                )
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "RAG tool setup failed, continuing without RAG tools",
                error=str(exc),
                error_type=type(exc).__name__,
            )

        try:
            tools.extend(
                await self._build_workflow_tools(
                    config.workflow,
                    request_id=request_id,
                    business_principal=business_principal,
                    business_context_token=business_context_token,
                    parent_workflow_execution_id=parent_workflow_execution_id,
                    workflow_lineage=workflow_lineage,
                )
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "Workflow tool setup failed",
                error_type=type(exc).__name__,
            )
            raise RuntimeError("员工工作流不可用，请检查固定版本及授权") from exc

        # Load MCP tools from the persisted agent binding. Per-run exclusions
        # are applied to a copy so the project's saved agent configuration is
        # never mutated.
        if internal_agent is not None:
            excluded_ids = set(excluded_tool_ids)
            runtime_agent = internal_agent
            if excluded_ids:
                runtime_agent = internal_agent.model_copy(
                    update={
                        "tools": [
                            tool
                            for tool in internal_agent.tools
                            if tool.tool_id not in excluded_ids
                        ]
                    }
                )
            if runtime_agent.tools:
                try:
                    tools.extend(
                        await self._build_mcp_tools_from_agent(
                            runtime_agent,
                            session_id,
                            user_id,
                            project_id=project_id,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    self._logger.warning(
                        "Bound tool setup failed; agent execution stopped",
                        error_type=type(exc).__name__,
                    )
                    raise MCPToolError(
                        "已绑定的工具暂时不可用，本次回答已停止；请检查工具连接或配置后重试。"
                    ) from None
        else:
            try:
                tools.extend(
                    await self._build_mcp_tools(
                        config.mcp_config, session_id, user_id
                    )
                )
            except (
                MCPConnectionError,
                MCPToolError,
                MCPAuthenticationError,
            ) as exc:
                self._logger.warning(
                    "MCP tool setup failed, continuing without MCP tools",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    (
                        'Unexpected error during MCP tool setup, continuing '
                        'without MCP tools'
                    ),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

        # Build device MCP tools if agent has a bound device
        if internal_agent and getattr(internal_agent, "bound_device_id", None):
            # A required device must not silently disappear from a control run.
            tools.extend(await self._build_device_mcp_tools(internal_agent))

        if agent_id:
            try:
                tools.extend(
                    self._build_custom_tools(
                        agent_id=agent_id,
                        session_id=session_id,
                        user_id=user_id,
                        project_id=project_id,
                        request_id=request_id,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    (
                        'Custom tool setup failed, continuing without custom '
                        'tools'
                    ),
                    error=str(exc),
                    error_type=type(exc).__name__,
                    agent_id=agent_id,
                )

        return tools

    def _build_custom_tools(
        self,
        *,
        agent_id: str,
        session_id: Optional[str],
        user_id: Optional[str],
        project_id: Optional[str],
        request_id: Optional[str],
    ) -> List[Any]:
        """Build agent-scoped custom tools for user operations and handoff."""
        from app.runtime.tools.custom import (
            create_handoff_tool,
            create_user_info_tool,
            create_user_sentiment_tool,
            create_user_tag_tool,
        )

        tools: List[Any] = []
        tool_context: _CustomToolArguments = {
            "agent_id": agent_id,
            "session_id": session_id,
            "user_id": user_id,
            "project_id": project_id,
            "request_id": request_id,
        }
        for creator in (
            create_handoff_tool,
            create_user_info_tool,
            create_user_sentiment_tool,
            create_user_tag_tool,
        ):
            created = creator(**tool_context)
            if isinstance(created, list):
                tools.extend(created)
            else:
                tools.append(created)

        return tools

    def _build_skills(self, project_id: str) -> Optional[Any]:
        """Build an Agno Skills object that loads project + official skills.

        Uses ``agno.skills.LocalSkills`` to scan skill directories.  The
        returned ``Skills`` object is passed directly to ``Agent(skills=...)``,
        which automatically:
        - Injects skill summaries into the system prompt
        - Registers ``get_skill_instructions`` / ``get_skill_reference`` /
          ``get_skill_script`` tools

        Disabled skills (tracked in ``.disabled_skills.json``) are excluded
        by loading each enabled skill individually via ``LocalSkills`` pointed
        at the specific skill folder (single-skill mode).

        Args:
            project_id: Project ID whose skill directory to load.

        Returns:
            An Agno ``Skills`` instance, or ``None`` if no skill directories
            contain any skills.
        """
        try:
            from agno.skills import LocalSkills, Skills
        except ImportError:
            self._logger.warning(
                "agno.skills not available; skipping skill loading"
            )
            return None

        from pathlib import Path

        from app.config import settings
        from app.services.skill_file_service import (
            SkillFileService,
            _parse_frontmatter,
        )

        base_dir = Path(settings.skills_base_dir)
        loaders: list[SkillLoader] = []

        # Load the set of disabled skill names for this project
        skill_service = SkillFileService(settings.skills_base_dir)
        disabled_skills = skill_service.get_disabled_skills(project_id)

        def _collect_enabled_loaders(directory: Path) -> None:
            (
                'Add a LocalSkills loader for each enabled skill in '
                '*directory*.'
            )
            if not directory.exists() or not directory.is_dir():
                return
            try:
                for child in sorted(directory.iterdir()):
                    if (
                        child.is_dir()
                        and not child.name.startswith(".")
                        and child.name not in disabled_skills
                        and (child / "SKILL.md").exists()
                    ):
                        frontmatter, _ = _parse_frontmatter(
                            (child / "SKILL.md").read_text(encoding="utf-8")
                        )
                        metadata = frontmatter.get("metadata") or {}
                        if (
                            isinstance(metadata, dict)
                            and metadata.get("skill_type") == "humanization"
                        ):
                            # Only the explicitly selected style belongs in the
                            # isolated final-expression pass, never factual
                            # tools.
                            continue
                        loaders.append(LocalSkills(str(child)))
            except OSError:
                pass

        # 1. Project-private skills
        _collect_enabled_loaders(base_dir / project_id)

        # 2. Official (shared) skills
        _collect_enabled_loaders(base_dir / "_official")

        if not loaders:
            self._logger.debug(
                "No enabled skill directories found",
                project_id=project_id,
                base_dir=str(base_dir),
            )
            return None

        self._logger.debug(
            "Building Skills object",
            project_id=project_id,
            loader_count=len(loaders),
            disabled_count=len(disabled_skills),
        )
        return Skills(loaders=loaders)

    async def _build_rag_tools(
        self,
        rag_config: Optional[RagConfig],
        *,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[Any]:
        if (
            not rag_config
            or not rag_config.rag_url
            or not rag_config.collections
        ):
            return []
        if rag_config.knowledge_channel is None:
            self._logger.warning(
                "Skipping RAG tools because knowledge_channel is missing",
                project_id=rag_config.project_id,
            )
            return []

        tools: List[Any] = []
        for collection in rag_config.collections:
            try:
                tool = await create_rag_tool(
                    rag_config.rag_url,
                    collection,
                    project_id=rag_config.project_id,
                    knowledge_channel=rag_config.knowledge_channel,
                    filters=rag_config.filters,
                    agent_id=agent_id,
                    session_id=session_id,
                    user_id=user_id,
                )
                tools.append(tool)
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    "Failed to create RAG tool for collection, skipping",
                    collection=collection,
                    rag_url=rag_config.rag_url,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
        return tools

    async def _build_workflow_tools(
        self,
        workflow_config: Optional[WorkflowConfig],
        *,
        request_id: str | None = None,
        business_principal: BusinessRequestPrincipal | None = None,
        business_context_token: SecretStr | None = None,
        parent_workflow_execution_id: UUID | None = None,
        workflow_lineage: tuple[str, ...] = (),
    ) -> List[Any]:
        if (
            not workflow_config
            or not workflow_config.workflow_url
            or not workflow_config.workflows
        ):
            return []

        try:
            return await create_workflow_tools(
                workflow_config.workflow_url,
                workflow_config.workflows,
                project_id=workflow_config.project_id,
                fixed_versions=workflow_config.release_versions,
                agent_id=workflow_config.agent_id,
                release_ids=workflow_config.release_ids,
                release_digests=workflow_config.release_digests,
                request_id=request_id,
                business_principal=business_principal,
                business_context_token=business_context_token,
                parent_workflow_execution_id=parent_workflow_execution_id,
                workflow_lineage=workflow_lineage,
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "Failed to create workflow tools",
                workflows=workflow_config.workflows,
                workflow_url=workflow_config.workflow_url,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise

    async def _build_mcp_tools(
        self,
        mcp_config: Optional[MCPConfig],
        session_id: Optional[str],
        user_id: Optional[str],
    ) -> List[Any]:
        if not mcp_config or not mcp_config.url:
            return []

        headers = await self._build_mcp_headers(
            mcp_config, session_id, user_id
        )
        server_url = mcp_config.url.rstrip("/") + "/mcp"
        requested_tools = set(mcp_config.tools or [])
        added_tool_names: set[str] = set()
        fetched_tools: List[Any] = []

        try:
            async with streamablehttp_client(
                server_url, headers=headers
            ) as streams:
                read_stream, write_stream, _ = streams
                async with ClientSession(read_stream, write_stream) as session:
                    try:
                        await session.initialize()
                    except Exception as exc:  # noqa: BLE001
                        raise MCPConnectionError(
                            "Failed to initialize MCP session",
                            mcp_url=server_url,
                            error=str(exc),
                        ) from exc

                    cursor = None
                    while True:
                        try:
                            tool_list_page = await session.list_tools(
                                cursor=cursor
                            )
                        except Exception as exc:  # noqa: BLE001
                            raise MCPToolError(
                                "Failed to list MCP tools",
                                mcp_url=server_url,
                                cursor=cursor,
                                error=str(exc),
                            ) from exc
                        if not tool_list_page or not tool_list_page.tools:
                            break

                        for mcp_tool in tool_list_page.tools:
                            if (
                                requested_tools
                                and mcp_tool.name not in requested_tools
                            ):
                                continue
                            if mcp_tool.name in added_tool_names:
                                continue

                            try:
                                agno_tool = create_agno_mcp_tool(
                                    mcp_tool,
                                    mcp_server_url=server_url,
                                    headers=headers,
                                )
                                if mcp_config.auth_required:
                                    agno_tool = wrap_mcp_authenticate_tool(
                                        agno_tool
                                    )
                                fetched_tools.append(agno_tool)
                                added_tool_names.add(mcp_tool.name)
                            except Exception as exc:  # noqa: BLE001
                                self._logger.warning(
                                    "Failed to convert MCP tool, skipping",
                                    tool_name=mcp_tool.name,
                                    mcp_url=server_url,
                                    error=str(exc),
                                    error_type=type(exc).__name__,
                                )

                        cursor = tool_list_page.nextCursor
                        if not cursor or (
                            requested_tools
                            and len(added_tool_names) == len(requested_tools)
                        ):
                            break
        except MCPConnectionError:
            raise
        except MCPToolError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MCPToolError(
                "Unexpected error during MCP tool setup",
                mcp_url=server_url,
                error=str(exc),
            ) from exc
        except (
            BaseException
        ) as exc:  # cancellation-like errors may inherit BaseException
            if self._is_cancellation_like_error(exc):
                raise MCPConnectionError(
                    "MCP setup canceled while initializing tools",
                    mcp_url=server_url,
                    error=str(exc),
                ) from exc
            raise

        self._logger.debug(
            "MCP tools setup completed",
            mcp_url=server_url,
            tools_fetched=len(fetched_tools),
            tools_requested=len(requested_tools) if requested_tools else "all",
        )

        return fetched_tools

    async def _fetch_mcp_tools_from_endpoint(
        self,
        endpoint: str,
        headers: Optional[Dict[str, str]] = None,
        requested_tools: Optional[set[str]] = None,
        *,
        project_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[Function]:
        """
        从 MCP 网关获取工具定义并创建 Function 对象。

        此方法通过 REST API 从 store-api 的 MCP 网关动态获取工具的真实定义，
        包括准确的 inputSchema，而不是依赖数据库中可能过时的静态配置。

        Args:
            endpoint: MCP 服务器地址（如 http://store-api/api/v1/mcp/{tool_id}/http）
            headers: 请求头（如认证信息）
            requested_tools: 指定要获取的工具名称集合，None 表示获取全部

        Returns:
            Function 对象列表
        """
        tools: List[Function] = []
        server_url = endpoint.rstrip("/")

        # 将 /http 端点转换为 /tools 端点
        # http://store-api/api/v1/mcp/{tool_id}/http ->
        # http://store-api/api/v1/mcp/{tool_id}/tools
        if server_url.endswith("/http"):
            tools_url = server_url[:-5] + "/tools"
        elif server_url.endswith("/sse"):
            tools_url = server_url[:-4] + "/tools"
        else:
            tools_url = server_url + "/tools"

        try:
            self._logger.info(
                "Fetching MCP tools from gateway",
                endpoint=endpoint,
                tools_url=tools_url,
                requested_tools=list(requested_tools)
                if requested_tools
                else None,
                header_keys=list(headers.keys()) if headers else None,
            )
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(tools_url, headers=headers)
                self._logger.info(
                    "MCP gateway response received",
                    tools_url=tools_url,
                    status_code=response.status_code,
                    content_type=response.headers.get("content-type"),
                )
                response.raise_for_status()

                data = response.json()
                print("data-->", data)
                self._logger.debug(
                    "MCP gateway response parsed",
                    tools_url=tools_url,
                    response_keys=list(data.keys())
                    if isinstance(data, dict)
                    else None,
                    tool_count=len(data.get("tools", []))
                    if isinstance(data, dict)
                    else None,
                )
                tool_list = data.get("tools", [])

                for tool_def in tool_list:
                    tool_name = tool_def.get("name")

                    # 如果指定了工具列表，只获取指定的工具
                    if requested_tools and tool_name not in requested_tools:
                        continue

                    # 创建一个类似 MCP Tool 的对象
                    mcp_tool = types.SimpleNamespace(
                        name=tool_name,
                        description=tool_def.get("description", ""),
                        inputSchema=tool_def.get(
                            "inputSchema", {"type": "object", "properties": {}}
                        ),
                    )

                    tool_func = create_agno_mcp_tool(
                        mcp_tool,
                        mcp_server_url=server_url,
                        headers=headers,
                        project_id=project_id,
                        agent_id=agent_id,
                        session_id=session_id,
                        user_id=user_id,
                    )
                    tools.append(tool_func)

                self._logger.debug(
                    "Successfully fetched tool definitions from MCP gateway",
                    tools_url=tools_url,
                    tool_count=len(tools),
                )

        except httpx.HTTPStatusError as e:
            response_text = ""
            try:
                response_text = e.response.text
            except Exception:
                response_text = ""
            if response_text and len(response_text) > 1000:
                response_text = response_text[:1000] + "...(truncated)"
            self._logger.warning(
                "MCP gateway returned error status",
                endpoint=endpoint,
                tools_url=tools_url,
                status_code=e.response.status_code,
                response_text=response_text,
            )
        except Exception as e:
            print(f"[ERROR] Failed to fetch MCP tools from endpoint: {e!r}")
            print(f"[ERROR] endpoint={endpoint}, tools_url={tools_url}")
            print(
                (
                    f'[ERROR] requested_tools='
                    f'{(list(requested_tools) if requested_tools else None)}'
                )
            )
            print(f"[ERROR] Traceback:\n{traceback.format_exc()}")
            self._logger.warning(
                (
                    f'Failed to fetch MCP tools from endpoint, tools will not '
                    f'be available. error='
                    f'{e!r}'
                ),
                endpoint=endpoint,
                tools_url=tools_url,
                requested_tools=list(requested_tools)
                if requested_tools
                else None,
                error_type=type(e).__name__,
            )

        return tools

    async def _build_mcp_tools_from_agent(
        self,
        internal_agent: "InternalAgent",
        session_id: Optional[str],
        user_id: Optional[str],
        project_id: Optional[str] = None,
    ) -> List[Any]:
        """Build MCP tools from InternalAgent.tools configuration."""
        # 1. Filter and group tools
        enabled_tools = [
            t
            for t in internal_agent.tools
            if (t.tool_type == "MCP" or t.transport_type == "http_webhook")
            and t.enabled
        ]
        if not enabled_tools:
            return []

        self._logger.debug(
            "Loading tools from agent",
            agent_id=str(internal_agent.id),
            count=len(enabled_tools),
        )

        # 2. Group by type/endpoint
        tools_by_endpoint: Dict[str, List[AgentTool]] = {}
        plugin_tools: List[AgentTool] = []
        http_tools: List[AgentTool] = []
        for t in enabled_tools:
            if t.transport_type == "plugin":
                plugin_tools.append(t)
            elif t.transport_type == "http_webhook":
                http_tools.append(t)
            elif t.endpoint:
                tools_by_endpoint.setdefault(t.endpoint, []).append(t)
            else:
                raise MCPToolError("Bound MCP tool requires an endpoint")

        # 3. Build authentication headers
        headers = await self._build_auth_headers(
            session_id, user_id, project_id
        )

        # 4. Build tool instances
        tools: List[Any] = []
        tools.extend(
            self._build_plugin_tools(
                plugin_tools,
                session_id,
                user_id,
                str(internal_agent.id),
                project_id,
            )
        )
        tools.extend(self._build_http_webhook_tools(http_tools))

        mcp_tools, _ = await self._build_mcp_server_instances(
            tools_by_endpoint,
            headers,
            project_id=project_id,
            agent_id=str(internal_agent.id),
            session_id=session_id,
            user_id=user_id,
        )
        tools.extend(mcp_tools)

        return tools

    async def _build_auth_headers(
        self,
        session_id: Optional[str],
        user_id: Optional[str],
        project_id: Optional[str],
    ) -> Dict[str, str]:
        """Helper to build consistent authentication headers."""
        headers = {}
        if session_id:
            headers["X-Session-ID"] = session_id
        if user_id:
            headers["X-User-ID"] = user_id

        if project_id:
            try:
                credential = await api_service_client.get_store_credential(
                    project_id
                )
                if credential and credential.get("api_key"):
                    headers["X-API-Key"] = credential["api_key"]
            except Exception:
                pass

        if "X-API-Key" not in headers and settings.store_api_key:
            headers["X-API-Key"] = settings.store_api_key
        return headers

    def _build_plugin_tools(
        self,
        plugin_tools: List[AgentTool],
        session_id: Optional[str],
        user_id: Optional[str],
        agent_id: str,
        project_id: Optional[str] = None,
    ) -> List[Any]:
        """Helper to construct plugin-based tools."""
        instances = []
        for t in plugin_tools:
            try:
                config = t.base_config or {}
                plugin_id = config.get("plugin_id")
                tool_name = config.get("tool_name")
                if not plugin_id or not tool_name:
                    raise ValueError(
                        "Bound plugin tool requires plugin_id and tool_name"
                    )

                # Build schema from parameters list
                props = {}
                req = []
                for p in config.get("parameters", []):
                    p_name = p.get("name")
                    p_type = p.get("type", "string")
                    prop = {
                        "type": "string" if p_type == "enum" else p_type,
                        "description": p.get("description", ""),
                    }
                    if p_type == "enum" and "enum_values" in p:
                        prop["enum"] = p["enum_values"]
                    props[p_name] = prop
                    if p.get("required"):
                        req.append(p_name)

                instances.append(
                    create_plugin_tool(
                        plugin_id=plugin_id,
                        tool_name=tool_name,
                        title=t.tool_name,
                        description=config.get("description"),
                        parameters={
                            "type": "object",
                            "properties": props,
                            "required": req,
                        }
                        if req
                        else {"type": "object", "properties": props},
                        session_id=session_id,
                        user_id=user_id,
                        agent_id=agent_id,
                        project_id=project_id,
                    )
                )
            except Exception as exc:
                self._logger.warning(
                    "Bound plugin tool setup failed",
                    error_type=type(exc).__name__,
                )
                raise MCPToolError(
                    "已绑定的工具暂时不可用，本次回答已停止；请检查工具连接或配置后重试。"
                ) from None
        return instances

    def _build_http_webhook_tools(
        self, http_tools: List[AgentTool]
    ) -> List[Any]:
        """Helper to construct HTTP webhook tools."""
        instances = []
        for t in http_tools:
            try:
                config = t.base_config or {}
                if not t.endpoint:
                    raise ValueError("Bound HTTP tool requires an endpoint")
                instances.append(
                    create_http_tool(
                        name=t.tool_name,
                        description=(
                            t.tool_description
                            or config.get("description")
                            or t.tool_name
                        ),
                        endpoint=t.endpoint,
                        method=config.get("method", "POST"),
                        headers=config.get("headers"),
                        parameters=config.get("parameters"),
                        timeout=config.get("timeout", 30.0),
                        logistics_provider=config.get("logistics_provider"),
                    )
                )
            except Exception as exc:
                self._logger.warning(
                    "Bound HTTP tool setup failed",
                    error_type=type(exc).__name__,
                )
                raise MCPToolError(
                    "已绑定的工具暂时不可用，本次回答已停止；请检查工具连接或配置后重试。"
                ) from None
        return instances

    def _build_store_tools_from_config(
        self,
        *,
        server_url: str,
        endpoint_tools: List[AgentTool],
        headers: Dict[str, str],
        project_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[Function]:
        (
            'Build Store MCP functions from persisted schemas without '
            'network I/O.'
        )

        type_mapping = {
            "str": "string",
            "string": "string",
            "int": "integer",
            "integer": "integer",
            "float": "number",
            "number": "number",
            "bool": "boolean",
            "boolean": "boolean",
            "list": "array",
            "array": "array",
            "dict": "object",
            "object": "object",
        }
        functions: List[Function] = []
        seen_names: set[str] = set()

        for endpoint_tool in endpoint_tools:
            config = endpoint_tool.base_config or {}
            methods = config.get("methods")
            if not isinstance(methods, dict):
                continue
            for method_name, method_config in methods.items():
                if (
                    not isinstance(method_name, str)
                    or method_name in seen_names
                ):
                    continue
                if not isinstance(method_config, dict):
                    method_config = {}
                properties: Dict[str, Any] = {}
                required: List[str] = []
                parameters = method_config.get("params") or []
                for parameter in parameters:
                    if not isinstance(parameter, dict):
                        continue
                    parameter_name = parameter.get("name")
                    if (
                        not isinstance(parameter_name, str)
                        or not parameter_name
                    ):
                        continue
                    source_type = str(
                        parameter.get("type") or "string"
                    ).lower()
                    schema: Dict[str, Any] = {
                        "type": type_mapping.get(source_type, "string"),
                        "description": str(parameter.get("description") or ""),
                    }
                    enum_values = parameter.get("enum")
                    if isinstance(enum_values, list) and enum_values:
                        schema["enum"] = enum_values
                    default_value = parameter.get("default")
                    if default_value is not None:
                        schema["default"] = default_value
                    properties[parameter_name] = schema
                    if parameter.get("required"):
                        required.append(parameter_name)

                mcp_tool = types.SimpleNamespace(
                    name=method_name,
                    description=str(
                        method_config.get("description") or method_name
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                )
                functions.append(
                    create_agno_mcp_tool(
                        mcp_tool,
                        mcp_server_url=server_url,
                        headers=headers,
                        project_id=project_id,
                        agent_id=agent_id,
                        session_id=session_id,
                        user_id=user_id,
                    )
                )
                seen_names.add(method_name)

        return functions

    async def _build_mcp_server_instances(
        self,
        tools_by_endpoint: Dict[str, List[AgentTool]],
        headers: Dict[str, str],
        *,
        project_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> tuple[List[Any], List[str]]:
        (
            'Helper to construct individual MCP server instances '
            '(HTTP/SSE/stdio).\n'
            '\n'
            '        Routing logic:\n'
            '        - STORE tools → ``_fetch_mcp_tools_from_endpoint`` '
            '(ToolStore gateway with API Key auth)\n'
            '        - LOCAL tools → ``MCPTools`` standard MCP direct '
            'connection (internal services)\n'
            '        '
        )
        instances: list[Function | SavedMCPTools] = []
        stdio_cmds: list[str] = []

        async def close_connected() -> None:
            # MCP clients own nested AnyIO scopes: release in reverse order.
            for instance in reversed(instances):
                if isinstance(instance, SavedMCPTools):
                    await instance.close()

        for endpoint, endpoint_tools in group_mcp_bindings(tools_by_endpoint):
            try:
                transport = endpoint_tools[0].transport_type or "http"
                if transport not in ("http", "sse", "stdio"):
                    raise MCPToolError("Unsupported bound MCP transport")
                server_url = (
                    endpoint if transport == "stdio" else endpoint.rstrip("/")
                )

                # Use tool_source_type to decide connection mode (not headers)
                is_store_tool = any(
                    t.tool_source_type == "STORE" for t in endpoint_tools
                )

                if is_store_tool and headers and transport != "stdio":
                    # Prefer the schema already synced into the database.
                    # Remote
                    # discovery added a fixed network delay to every chat turn.
                    fetched = self._build_store_tools_from_config(
                        server_url=server_url,
                        endpoint_tools=endpoint_tools,
                        headers=headers,
                        project_id=project_id,
                        agent_id=agent_id,
                        session_id=session_id,
                        user_id=user_id,
                    )
                    if not fetched:
                        fetched = await self._fetch_mcp_tools_from_endpoint(
                            server_url,
                            headers,
                            project_id=project_id,
                            agent_id=agent_id,
                            session_id=session_id,
                            user_id=user_id,
                        )
                    if fetched:
                        instances.extend(fetched)
                    else:
                        raise MCPToolError(
                            "MCP discovery returned no callable tools"
                        )
                else:
                    # Standard MCP direct connection (LOCAL tools / internal
                    # services)
                    mcp = SavedMCPTools(
                        transport="stdio"
                        if transport == "stdio"
                        else "http"
                        if transport == "http"
                        else "sse",
                        url=server_url,
                        headers=mcp_connection_headers(
                            endpoint_tools[0].base_config
                        ),
                        tool_names=[tool.tool_name for tool in endpoint_tools],
                    )
                    await mcp.connect()
                    instances.append(mcp)
                    if not {
                        tool.tool_name for tool in endpoint_tools
                    }.issubset(mcp.functions):
                        raise MCPToolError(
                            "MCP server is missing a bound tool"
                        )
            except Exception as exc:
                self._logger.warning(
                    "Bound MCP server setup failed",
                    error_type=type(exc).__name__,
                )
                await close_connected()
                raise MCPToolError(
                    "已绑定的工具暂时不可用，本次回答已停止；请检查工具连接或配置后重试。"
                ) from None
            except BaseException:
                await close_connected()
                raise
        return instances, stdio_cmds

    async def _build_device_mcp_tools(
        self, internal_agent: "InternalAgent"
    ) -> List[Any]:
        """Create callable MCP function tools for the agent's bound device.

        Unlike generic MCP toolkit wiring, this resolves device MCP tools at
        build time and returns function tools directly. This avoids runtime
        toolkit initialization failures from aborting the whole agent stream.
        """
        device_id = internal_agent.bound_device_id
        if not device_id:
            return []

        project_id = internal_agent.project_id
        if not project_id:
            raise MCPAuthenticationError(
                "Device access requires the agent's project"
            )
        # Validate identifiers before inserting them into a trusted endpoint
        # URL.
        device_id = str(UUID(device_id))
        project_id = str(UUID(project_id))

        try:
            read_policy = await device_control_client.validate_binding(
                UUID(project_id), device_id
            )
        except (ExternalServiceError, NotFoundError, ValidationError) as exc:
            raise MCPToolError("设备策略或测试暂不可用，请检查设备后重试") from exc
        current_digest = read_policy.digest if read_policy else None
        if current_digest != getattr(
            internal_agent, "bound_device_policy_digest", None
        ):
            raise MCPToolError("设备策略已变化，请重新验证并绑定此设备")

        device_headers = device_headers_factory(
            project_id,
            device_id,
            getattr(internal_agent, "bound_device_policy_digest", None),
        )

        endpoint = settings.device_control_mcp_endpoint.replace(
            "{device_id}", str(device_id)
        )
        self._logger.info(
            "Connecting to device MCP",
            device_id=device_id,
            endpoint=endpoint,
        )

        # Generic MCP bindings are separate tools, not a device permissions
        # list.
        added_tool_names: set[str] = set()
        device_tools: List[Any] = []

        try:
            async with streamablehttp_client(
                endpoint, headers=device_headers()
            ) as streams:
                read_stream, write_stream, _ = streams
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()

                    cursor = None
                    while True:
                        tool_list_page = await session.list_tools(
                            cursor=cursor
                        )
                        if not tool_list_page or not tool_list_page.tools:
                            break

                        for mcp_tool in tool_list_page.tools:
                            if mcp_tool.name in added_tool_names:
                                continue

                            try:
                                device_tools.append(
                                    create_agno_mcp_tool(
                                        mcp_tool,
                                        mcp_server_url=endpoint,
                                        headers_factory=device_headers,
                                        project_id=project_id,
                                        agent_id=str(internal_agent.id),
                                    )
                                )
                                added_tool_names.add(mcp_tool.name)
                            except Exception as exc:  # noqa: BLE001
                                self._logger.warning(
                                    (
                                        'Failed to convert device MCP tool, '
                                        'skipping'
                                    ),
                                    device_id=str(device_id),
                                    endpoint=endpoint,
                                    tool_name=mcp_tool.name,
                                    error=str(exc),
                                    error_type=type(exc).__name__,
                                )

                        cursor = tool_list_page.nextCursor
                        if not cursor:
                            break
        except Exception as exc:  # noqa: BLE001
            raise MCPConnectionError(
                "Device MCP setup failed during tool discovery",
                mcp_url=endpoint,
                error=str(exc),
            ) from exc
        except BaseException as exc:
            if self._is_cancellation_like_error(exc):
                raise MCPConnectionError(
                    "Device MCP setup canceled during tool discovery",
                    mcp_url=endpoint,
                    error=str(exc),
                ) from exc
            raise

        if not device_tools:
            raise MCPConnectionError("绑定设备未连接或暂无可用工具", mcp_url=endpoint)
        self._logger.debug(
            "Device MCP tools setup completed",
            device_id=str(device_id),
            endpoint=endpoint,
            tools_fetched=len(device_tools),
        )
        return device_tools

    def get_memory_backend(
        self, model: Any
    ) -> tuple[MemoryManager, PostgresDb]:
        (
            'Expose shared memory backend for external consumers (keyed by '
            'model).'
        )
        return self._ensure_memory_backend(model)

    def _ensure_memory_backend(
        self, model: Any
    ) -> tuple[MemoryManager, PostgresDb]:
        (
            'Always create a fresh MemoryManager for the provided model; '
            'reuse only the PostgresDb connection.'
        )
        try:
            db = self._memory_db
            if db is None:
                db_url = settings.get_database_url(sync=True)
                self._logger.debug(
                    "Initializing Postgres memory backend", db_url=db_url
                )
                db = PostgresDb(db_url=db_url)
                self._memory_db = db

            # Create a new MemoryManager for each request to avoid stale model
            # references
            memory_manager = MemoryManager(model=model, db=db)
            return memory_manager, db
        except Exception as exc:  # noqa: BLE001
            self._logger.error(
                "Failed to initialize memory manager",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise InvalidConfigurationError(
                "Failed to initialize memory backend",
                error=str(exc),
            ) from exc

    async def _build_mcp_headers(
        self,
        mcp_config: MCPConfig,
        session_id: Optional[str],
        user_id: Optional[str],
    ) -> Optional[Dict[str, str]]:
        if not mcp_config.auth_required:
            return None
        if not mcp_config.url:
            raise MCPAuthenticationError(
                "MCP service URL is required for authentication"
            )

        supabase_token = self._settings.supabase.access_token
        if not supabase_token:
            self._logger.debug(
                "No Supabase token available for MCP authentication"
            )
            raise MCPAuthenticationError(
                "Supabase access token is required for MCP authentication"
            )

        try:
            tokens = await get_mcp_access_token(supabase_token, mcp_config.url)
            access_token = tokens.access_token.get_secret_value()
        except Exception as exc:  # noqa: BLE001
            raise MCPAuthenticationError(
                "Failed to fetch MCP access token",
                error_type=type(exc).__name__,
            ) from None

        return {"Authorization": f"Bearer {access_token}"}

    # ------------------------------------------------------------------
    # Model helpers
    def initialize_model(
        self, config: AgentConfig
    ) -> Any:  # pragma: no cover - backwards compatibility
        return self._initialize_model(config)

    def _initialize_model(self, config: AgentConfig) -> Model:
        model_name = config.model_name or self._settings.model.name
        if not model_name:
            raise MissingConfigurationError(
                "Model name is required", config_key="model_name"
            )

        creds = config.provider_credentials
        if not creds:
            raise MissingConfigurationError(
                "LLM provider credentials required", model_name=model_name
            )

        api_key = creds.api_key
        if not api_key:
            raise MissingConfigurationError(
                f"Missing API key for {model_name}", model_name=model_name
            )

        # Validation
        if config.temperature is not None and not (
            0 <= config.temperature <= 2
        ):
            raise InvalidConfigurationError(
                "Temperature must be 0-2", temperature=config.temperature
            )
        if config.max_tokens is not None and config.max_tokens <= 0:
            raise InvalidConfigurationError(
                "max_tokens must be positive", max_tokens=config.max_tokens
            )

        provider_kind = (creds.provider_kind or "").lower()
        provider_vendor = (creds.vendor or "").lower()
        provider_base_url = (creds.api_base_url or "").lower()
        try:
            if provider_kind in {"openai", "openai_compatible"}:
                is_deepseek = (
                    provider_vendor == "deepseek"
                    or "api.deepseek.com" in provider_base_url
                )
                thinking_enabled = config.thinking_enabled
                if (
                    thinking_enabled is None
                    and is_deepseek
                    and model_name.lower() == "deepseek-v4-flash"
                ):
                    # Flash is the latency-oriented customer-service model.
                    # DeepSeek V4 defaults to thinking mode, which adds several
                    # seconds even for short FAQ replies.
                    thinking_enabled = False
                extra_body: dict[str, dict[str, str]] | None = None
                if is_deepseek and thinking_enabled is not None:
                    extra_body = {
                        "thinking": {
                            "type": "enabled"
                            if thinking_enabled
                            else "disabled"
                        }
                    }
                return OpenAIChat(
                    id=model_name,
                    api_key=api_key,
                    temperature=config.temperature,
                    max_tokens=config.max_tokens,
                    role_map={
                        "system": "system",
                        "user": "user",
                        "assistant": "assistant",
                        "tool": "tool",
                        "model": "assistant",
                    },
                    base_url=creds.api_base_url,
                    organization=creds.organization,
                    timeout=creds.timeout,
                    extra_body=extra_body,
                )

            if provider_kind == "anthropic":
                claude = Claude(
                    id=model_name,
                    api_key=api_key,
                    temperature=config.temperature,
                    timeout=creds.timeout,
                    client_params={"base_url": creds.api_base_url}
                    if creds.api_base_url
                    else None,
                )
                # Omitting the token limit must retain the SDK's non-null
                # default.
                if config.max_tokens is not None:
                    claude.max_tokens = config.max_tokens
                return claude

            if provider_kind == "google":
                http_options = None
                if creds.api_base_url is not None or creds.timeout is not None:
                    # The Google SDK expects HTTP options on its client and
                    # milliseconds.
                    http_options = HttpOptions(
                        base_url=creds.api_base_url,
                        timeout=ceil(creds.timeout * 1000)
                        if creds.timeout is not None
                        else None,
                    )
                return Gemini(
                    id=model_name,
                    api_key=api_key,
                    temperature=config.temperature,
                    max_output_tokens=config.max_tokens,
                    client_params={"http_options": http_options}
                    if http_options is not None
                    else None,
                )

        except Exception as exc:
            raise InvalidConfigurationError(
                f"Failed to init {provider_kind} model",
                model_name=model_name,
                error_type=type(exc).__name__,
            ) from None

        raise InvalidConfigurationError(
            f"Unsupported provider: {provider_kind}",
            model_name=model_name,
            supported=["openai", "openai_compatible", "anthropic", "google"],
        )
