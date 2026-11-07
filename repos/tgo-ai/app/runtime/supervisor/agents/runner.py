"""Run direct single-agent supervisor executions."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import Any, Optional
from uuid import UUID

from agno.agent import (
    RunCancelledEvent,
    RunCompletedEvent,
    RunContentEvent,
    RunErrorEvent,
    ToolCallCompletedEvent,
    ToolCallStartedEvent,
)

from app.core.logging import get_logger
from app.services.model_usage import track_model_usage
from app.services.agent_execution_scope import check_current_execution
from app.models.internal import AgentExecutionContext
from app.runtime.supervisor.streaming.workflow_events import (
    WorkflowEventEmitter,
)
from app.schemas.agent_run import (
    AgentExecutionResult,
    AgentRunMetadata,
    SupervisorRunResponse,
)
from app.schemas.knowledge_evidence import BusinessToolEvidence
from app.models.streaming import AgentBusinessQueryLink

from .builder import BuiltAgent


@dataclass
class AgentRunResult:
    """Container for final single-agent execution artifacts."""

    content: str
    total_time: float
    success: bool
    error: Optional[str] = None


class AgnoAgentRunner:
    """Execute a direct Agno agent with single-agent response semantics."""

    def __init__(self) -> None:
        self._logger = get_logger("runtime.supervisor.agents.runner")

    @staticmethod
    def _configured_prompt_sha256(agent: object) -> str | None:
        """Fingerprint the local prompt fields supplied to Agno for this run."""
        instructions = getattr(agent, "instructions", None)
        additional_context = getattr(agent, "additional_context", None)
        if not isinstance(instructions, str) or not (
            additional_context is None or isinstance(additional_context, str)
        ):
            return None
        serialized = json.dumps(
            {
                "instructions": instructions,
                "additional_context": additional_context or "",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    async def run(
        self, built_agent: BuiltAgent, context: AgentExecutionContext
    ) -> SupervisorRunResponse:
        """Run one agent and translate its result into the public response schema."""
        start_time = time.time()
        async with track_model_usage(
            context.project_id, context.agent.model, context.response_purpose
        ) as usage:
            output = await built_agent.agent.arun(
                context.message,
                stream=False,
                run_id=str(context.execution_id),
                session_id=context.session_id,
                user_id=context.user_id,
            )
            usage.observe(getattr(output, "metrics", None))
            await check_current_execution()
            status = getattr(output, "status", None)
            status_value = getattr(status, "value", status)
            final_content = self._extract_final_content(output)
            if (
                status_value is not None and status_value != "COMPLETED"
            ) or not final_content:
                usage.status = (
                    "cancelled" if status_value == "CANCELLED" else "failed"
                )
                return SupervisorRunResponse(
                    success=False,
                    message="AI 生成未完成",
                    content="",
                    error="模型未完成可发送的答复，请重试或转人工处理",
                    execution_id=context.execution_id,
                )
        total_time = time.time() - start_time
        tools_used = self._extract_tool_names(getattr(output, "tools", None))
        if context.knowledge_evidence is not None:
            for tool in getattr(output, "tools", None) or []:
                self._capture_tool_evidence(context, tool)
        result = AgentExecutionResult(
            agent_id=context.agent.id,
            agent_name=context.agent.name,
            question=context.message,
            content=final_content,
            tools_used=tools_used or None,
            execution_time=total_time,
            success=True,
            error=None,
        )
        metadata = AgentRunMetadata(
            agent_id=context.agent.id,
            agent_name=context.agent.name,
            total_execution_time=total_time,
            session_id=context.session_id,
            model_name=(
                None
                if context.agent.is_remote_store_agent
                else context.agent.model
            ),
            configured_prompt_sha256=(
                None
                if context.agent.is_remote_store_agent
                else self._configured_prompt_sha256(built_agent.agent)
            ),
        )
        return SupervisorRunResponse(
            success=True,
            execution_id=context.execution_id,
            execution_reference=getattr(context, "execution_reference", None),
            message="Agent run completed",
            **(
                {"knowledge_evidence": context.knowledge_evidence}
                if context.knowledge_evidence is not None
                else {}
            ),
            result=result,
            content=final_content,
            metadata=metadata,
            error=None,
        )

    async def stream(
        self,
        built_agent: BuiltAgent,
        context: AgentExecutionContext,
        workflow_events: WorkflowEventEmitter,
        execution_id: str,
        *,
        buffered_draft: bool = False,
    ) -> AgentRunResult:
        """Run a single agent with streaming workflow events."""
        start_time = time.time()
        content_chunks: list[str] = []
        completed_content = ""
        completed_seen = False
        success = True
        error: Optional[str] = None
        chunk_index = 0
        tool_calls = 0
        reported_query_links: dict[UUID, AgentBusinessQueryLink] = {}
        next_draft_check = 0.0

        async with track_model_usage(
            context.project_id, context.agent.model, context.response_purpose
        ) as usage:
            async for event in built_agent.agent.arun(
                context.message,
                stream=True,
                stream_events=True,
                run_id=execution_id,
                session_id=context.session_id,
                user_id=context.user_id,
            ):
                # The API holds customer drafts until final publication. Only
                # nearby text chunks may share a check within this interval.

                if not (
                    buffered_draft
                    and isinstance(event, RunContentEvent)
                    and monotonic() < next_draft_check
                ):
                    await check_current_execution()
                    if buffered_draft:
                        next_draft_check = monotonic() + 0.5
                timestamp = datetime.now(timezone.utc).isoformat()
                if isinstance(event, RunContentEvent):
                    content = event.content or ""
                    if content:
                        content_chunks.append(content)
                        workflow_events.emit_agent_content_chunk(
                            agent_id=str(context.agent.id),
                            agent_name=context.agent.name,
                            execution_id=execution_id,
                            content_chunk=content,
                            chunk_index=chunk_index,
                            is_final=False,
                        )
                        chunk_index += 1
                    continue
                if isinstance(event, ToolCallStartedEvent) and event.tool:
                    tool_calls += 1
                    workflow_events.emit_agent_tool_call_started(
                        agent_id=str(context.agent.id),
                        agent_name=context.agent.name,
                        execution_id=execution_id,
                        tool_name=getattr(
                            event.tool, "tool_name", "unknown_tool"
                        ),
                        tool_call_id=getattr(event.tool, "tool_call_id", None),
                        tool_input=(
                            None
                            if str(
                                getattr(event.tool, "tool_name", "")
                            ).startswith("employee_business_")
                            else getattr(event.tool, "tool_args", None)
                        ),
                    )
                    continue
                if isinstance(event, ToolCallCompletedEvent) and event.tool:
                    self._capture_tool_evidence(context, event.tool)
                    business_query_links = self._new_business_query_links(
                        context,
                        reported_query_links,
                    )
                    tool_name = str(
                        getattr(event.tool, "tool_name", "unknown_tool")
                    )
                    is_business_query = tool_name.startswith(
                        "employee_business_"
                    )
                    workflow_events.emit_agent_tool_call_completed(
                        agent_id=str(context.agent.id),
                        agent_name=context.agent.name,
                        execution_id=execution_id,
                        tool_name=tool_name,
                        tool_call_id=getattr(event.tool, "tool_call_id", None),
                        tool_input=(
                            None
                            if is_business_query
                            else getattr(event.tool, "tool_args", None)
                        ),
                        tool_output=(
                            None
                            if is_business_query
                            else getattr(event.tool, "result", None)
                        ),
                        tool_call_error=bool(
                            getattr(event.tool, "tool_call_error", False)
                        ),
                        business_query_links=business_query_links,
                    )
                    continue
                if isinstance(event, RunCompletedEvent):
                    usage.observe(getattr(event, "metrics", None))
                    completed_seen = True
                    completed_content = self._ensure_text(
                        getattr(event, "content", None)
                    )
                    continue
                if isinstance(event, RunErrorEvent):
                    usage.failed()
                    usage.observe(getattr(event, "metrics", None))
                    success = False
                    error = (
                        event.content or event.error_type or "Agent run failed"
                    )
                    self._logger.error(
                        "Agent streaming run failed",
                        agent_id=str(context.agent.id),
                        request_id=context.request_id,
                        timestamp=timestamp,
                        error=error,
                    )
                    continue
                if isinstance(event, RunCancelledEvent):
                    usage.status = "cancelled"
                    success = False
                    error = event.reason or "Agent run cancelled"
                    self._logger.info(
                        "Agent streaming run cancelled",
                        agent_id=str(context.agent.id),
                        request_id=context.request_id,
                        timestamp=timestamp,
                        error=error,
                    )
            if not completed_seen and success:
                usage.failed()
                success = False
                error = "Agent stream ended without a completion event"
        streamed_content = "".join(content_chunks)
        final_content = completed_content or streamed_content
        await check_current_execution()
        workflow_events.emit_agent_response_complete(
            agent_id=str(context.agent.id),
            agent_name=context.agent.name,
            execution_id=execution_id,
            final_content=final_content,
            success=success,
            total_chunks=chunk_index,
            tool_calls_count=tool_calls,
            knowledge_evidence=context.knowledge_evidence,
            execution_reference=getattr(context, "execution_reference", None),
        )
        return AgentRunResult(
            content=final_content,
            total_time=time.time() - start_time,
            success=success,
            error=error,
        )

    @staticmethod
    def _new_business_query_links(
        context: AgentExecutionContext,
        reported: dict[UUID, AgentBusinessQueryLink],
    ) -> list[AgentBusinessQueryLink]:
        """Drain query receipts for this execution without guessing tool order."""
        evidence = context.knowledge_evidence
        if evidence is None:
            return []
        actions = {
            "order_query": "order",
            "logistics_query": "logistics",
            "customer_profile_query": "profile",
            "membership_query": "member",
        }
        links: list[AgentBusinessQueryLink] = []
        for tool in evidence.tool_results:
            if tool.query_id is None and tool.operation is None:
                continue
            action = actions.get(tool.operation or "")
            if (
                tool.query_id is None
                or tool.operation is None
                or action is None
                or not tool.name.startswith(f"employee_business_{action}_")
            ):
                raise ValueError("Invalid business query trace")
            link = AgentBusinessQueryLink(
                tool_name=tool.name,
                query_id=tool.query_id,
                operation=tool.operation,
                outcome="passed" if tool.success else "failed",
            )
            prior = reported.get(link.query_id)
            if prior is not None:
                if prior != link:
                    raise ValueError("Business query trace changed")
                continue
            reported[link.query_id] = link
            links.append(link)
        return links

    @staticmethod
    def _capture_tool_evidence(
        context: AgentExecutionContext,
        tool: object,
    ) -> None:
        if context.knowledge_evidence is None:
            return
        name = str(getattr(tool, "tool_name", "unknown_tool"))
        # Memory is context, never an authoritative business tool receipt.

        if (
            name.startswith(
                (
                    "rag_search_",
                    "get_skill",
                    "read_skill",
                    "employee_business_",
                )
            )
            or "memory" in name.lower()
        ):
            return
        content = AgnoAgentRunner._ensure_text(getattr(tool, "result", None))
        context.knowledge_evidence.tool_results.append(
            BusinessToolEvidence(
                name=name,
                content=content,
                success=not bool(getattr(tool, "tool_call_error", False)),
            )
        )

    @staticmethod
    def _ensure_text(value: Optional[Any]) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return str(value)

    @classmethod
    def _extract_final_content(cls, output: Any) -> str:
        """Return the final visible assistant turn, excluding tool-call preambles."""
        messages = getattr(output, "messages", None)
        if isinstance(messages, (list, tuple)):
            for message in reversed(messages):
                role = getattr(message, "role", None)
                if role is not None and hasattr(role, "value"):
                    role = role.value
                normalized_role = str(role or "").lower().rsplit(".", 1)[-1]
                if normalized_role != "assistant":
                    continue
                if getattr(message, "tool_calls", None):
                    continue
                content = cls._ensure_text(
                    getattr(message, "content", None)
                ).strip()
                if content:
                    return content
        return cls._ensure_text(getattr(output, "content", None)).strip()

    @staticmethod
    def _extract_tool_names(tools: Any) -> list[str]:
        if not tools:
            return []
        names: list[str] = []
        for tool in tools:
            name = getattr(tool, "tool_name", None)
            if isinstance(name, str) and name:
                names.append(name)
        return names
