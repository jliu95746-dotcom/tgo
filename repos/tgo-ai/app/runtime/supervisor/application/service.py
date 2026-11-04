"""Supervisor runtime service implemented via direct single-agent execution."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from typing import AsyncIterator, Dict, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request
from starlette.responses import StreamingResponse

from app.core.logging import get_logger
from app.exceptions import NotFoundError
from app.models.internal import AgentExecutionContext, Agent
from app.config import settings
from app.schemas.agent_trials import AgentTrialRequest
from app.services.default_knowledge import resolve_agent_knowledge
from app.schemas.knowledge_evidence import KnowledgeEvidence
from app.services.current_knowledge import SOCIAL_ONLY, retrieve_current_knowledge
from app.runtime.supervisor.agents.builder import AgnoAgentBuilder
from app.runtime.supervisor.agents.runner import AgnoAgentRunner
from app.runtime.supervisor.infrastructure.services import AIServiceClient
from app.runtime.supervisor.streaming.workflow_events import create_workflow_events
from app.runtime.tools.executor.service import ToolsRuntimeService
from app.runtime.tools.mcp_lifecycle import mcp_request_scope
from app.schemas.agent_run import SupervisorRunRequest, SupervisorRunResponse
from app.services.agent_service import AgentService
from app.services.agent_execution_scope import check_current_execution, track_execution_scope
from app.schemas.agent_execution import AgentExecutionReference
from app.schemas.business_execution_context import BusinessContextCheck, business_request_fingerprint
from app.services.api_service import api_service_client
from pydantic import SecretStr
from app.schemas.knowledge import KnowledgeChannel
from app.services.device_execution import DeviceExecution, track_device_execution
from app.services.reply_phase_receipts import report_phase_ended
from app.streaming.event_emitter import get_event_emitter
from app.streaming.sse_handler import create_sse_response
from app.streaming.owned_response import (
    finish_execution,
    own_execution,
    run_until_disconnect,
)


@dataclass
class RunRegistryEntry:
    """Typed entry for a running single-agent execution."""

    task: asyncio.Task[None]
    project_id: str
    request_id: str
    correlation_id: str
    execution_id: str
    agent_id: str
    agent_name: str
    started_at: float
    device_execution: DeviceExecution | None = None
    accepting_cancel: bool = True
    cancel_requested: bool = False


class SupervisorRuntimeService:
    """High-level facade coordinating direct single-agent runtime execution."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        tools_runtime_service: ToolsRuntimeService,
    ) -> None:
        self._session_factory = session_factory
        self._tools_runtime = tools_runtime_service
        self._logger = get_logger("runtime.supervisor.service")
        runtime_settings = getattr(self._tools_runtime, "_settings", None)
        self._agent_builder = AgnoAgentBuilder(runtime_settings)
        self._agent_runner = AgnoAgentRunner()
        self._runs: Dict[str, RunRegistryEntry] = {}
        self._runs_lock = asyncio.Lock()
        from app.services.agent_execution_reference import AgentExecutionReferenceReader
        self._execution_reader = AgentExecutionReferenceReader(session_factory)

    async def run(
        self,
        payload: SupervisorRunRequest,
        project_id: uuid.UUID,
        extra_headers: Optional[Dict[str, str]] = None,
        *,
        http_request: Request | None = None,
    ) -> SupervisorRunResponse:
        """Execute a single-agent request and return the unified response."""
        self._validate_reply_phase(payload, project_id)
        if payload.cancel_on_disconnect:
            if http_request is None:
                raise ValueError("HTTP request required for owned execution")
            try:
                return await run_until_disconnect(
                    lambda: self._run(payload, project_id, extra_headers), http_request
                )
            finally:
                await report_phase_ended(payload.reply_phase)
        return await self._run(payload, project_id, extra_headers)

    @staticmethod
    def _validate_reply_phase(
        payload: SupervisorRunRequest, project_id: uuid.UUID
    ) -> None:
        if payload.reply_phase is not None and (
            not payload.cancel_on_disconnect
            or payload.reply_phase.project_id != str(project_id)
        ):
            raise ValueError("Reply phase project does not match the execution")

    async def _run(
        self,
        payload: SupervisorRunRequest,
        project_id: uuid.UUID,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> SupervisorRunResponse:
        headers = self._build_auth_headers(project_id, extra_headers)
        context: AgentExecutionContext | None = None

        try:
            context, _ = await self._prepare_context(payload, project_id, headers)
            if getattr(context, "execution_id", None) is None:
                context.execution_id = uuid.uuid4()
            async with track_execution_scope(getattr(context, "execution_reference", None), self._execution_reader), track_device_execution(context) as execution, mcp_request_scope():
                await check_current_execution()
                built_agent = await self._agent_builder.build_agent(context)
                await check_current_execution()
                self._logger.debug(
                    "Starting supervisor run",
                    agent_id=str(context.agent.id),
                    request_id=context.request_id,
                )
                result = await self._agent_runner.run(built_agent, context)
                await check_current_execution()
                if execution is not None:
                    execution.complete(result.success)
                return result
        except NotFoundError as exc:
            return self._build_failure_response(
                str(exc), context.execution_id if context is not None else None
            )
        except ValueError as exc:
            return self._build_failure_response(
                str(exc), context.execution_id if context is not None else None
            )
        except Exception as exc:  # pragma: no cover - defensive runtime path
            self._logger.exception(
                "Supervisor run failed",
                project_id=str(project_id),
                request_id=headers.get("X-Request-ID"),
            )
            return self._build_failure_response(
                str(exc) or "Agent run failed",
                context.execution_id if context is not None else None,
            )

    async def run_knowledge_trial(
        self, agent: Agent, request: AgentTrialRequest,
    ) -> SupervisorRunResponse:
        """Run governed knowledge without customer history, skills or actions."""
        if agent.is_remote_store_agent:
            raise ValueError("远程员工尚未提供可核验的独立试聊能力")
        if not agent.project_id:
            raise ValueError("无法核验员工归属")
        context = AgentExecutionContext(
            agent=agent.model_copy(deep=True), project_id=agent.project_id,
            message=request.message, request_id=str(request.request_key),
            timeout=request.timeout, rag_url=settings.rag_service_url,
            knowledge_channel=request.channel, require_current_knowledge=True,
            enable_memory=False, disable_tools=False, ui_mode="text",
            system_message=None, expected_output=None,
            session_id=None, user_id=None, mcp_url=None,
            markdown=None, temperature=None,
        )
        context.agent = await resolve_agent_knowledge(context.agent, request.channel)
        context.knowledge_evidence = await retrieve_current_knowledge(context)
        if context.knowledge_evidence.status in {"unavailable", "over_budget"}:
            # An independent knowledge test cannot verify an answer without
            # retrieval. Do not spend the remaining deadline on generation and
            # obscure the retrieval failure with a generic model timeout.
            return SupervisorRunResponse(
                success=False, message="Knowledge trial retrieval blocked",
                content="", knowledge_evidence=context.knowledge_evidence,
            )
        context.agent = context.agent.model_copy(update={
            "tools": [], "workflows": [], "bound_device_id": None,
            "skills_enabled": False,
        })
        context.disable_tools = True
        built = await self._agent_builder.build_agent(context)
        response = await self._agent_runner.run(built, context)
        return response.model_copy(update={"knowledge_evidence": context.knowledge_evidence})

    async def stream(
        self,
        payload: SupervisorRunRequest,
        project_id: uuid.UUID,
        extra_headers: Optional[Dict[str, str]] = None,
        http_request: Request | None = None,
    ) -> StreamingResponse:
        """Execute a single-agent request with Server-Sent Events streaming."""
        self._validate_reply_phase(payload, project_id)
        if http_request is None:
            raise RuntimeError("HTTP request object required for streaming")

        auth_headers = self._build_auth_headers(project_id, extra_headers)
        request_id = auth_headers.get("X-Request-ID", str(uuid.uuid4()))
        correlation_id = str(uuid.uuid4())

        event_emitter = get_event_emitter(request_id, correlation_id)
        event_emitter.enable_streaming()
        workflow_events = create_workflow_events(event_emitter)

        async def coordination_task() -> None:
            execution_id: Optional[str] = None
            entry: RunRegistryEntry | None = None
            try:
                context, _ = await self._prepare_context(
                    payload, project_id, auth_headers
                )
                if getattr(context, "execution_id", None) is None:
                    context.execution_id = uuid.uuid4()
                workflow_events.emit_workflow_started(request_id, context)
                async with track_execution_scope(getattr(context, "execution_reference", None), self._execution_reader), track_device_execution(context) as execution, mcp_request_scope():
                    await check_current_execution()
                    built_agent = await self._agent_builder.build_agent(context)
                    await check_current_execution()
                    execution_id = str(context.execution_id)

                    owner = asyncio.current_task()
                    assert owner is not None
                    entry = RunRegistryEntry(
                        task=owner,
                        project_id=str(project_id),
                        request_id=request_id,
                        correlation_id=correlation_id,
                        execution_id=execution_id,
                        agent_id=str(context.agent.id),
                        agent_name=context.agent.name,
                        started_at=time.time(),
                        device_execution=execution,
                    )
                    await self._register_run(execution_id, entry)

                    workflow_events.emit_agent_execution_started(
                        agent_id=str(context.agent.id),
                        agent_name=context.agent.name,
                        execution_id=execution_id,
                        question=context.message,
                    )
                    try:
                        stream_agent = self._agent_runner.stream
                        if (
                            payload.reply_phase is not None
                            and payload.require_current_knowledge
                            and "X-Execution-Channel" in auth_headers
                        ):
                            stream_agent = partial(
                                stream_agent, buffered_draft=True,
                            )
                        agent_result = await stream_agent(
                            built_agent,
                            context,
                            workflow_events,
                            execution_id,
                        )
                        await check_current_execution()
                        # A dependency swallowing CancelledError must not turn
                        # an accepted stop into a successful final answer.
                        if entry.cancel_requested:
                            raise asyncio.CancelledError
                    finally:
                        # Cleanup is not a new cancellable execution phase.
                        entry.accepting_cancel = False
                    if execution is not None:
                        execution.complete(agent_result.success)
                if agent_result.success:
                    workflow_events.emit_workflow_completed(agent_result.total_time, 1)
                else:
                    workflow_events.emit_workflow_failed(
                        agent_result.error or "Agent execution failed",
                        "agent_execution",
                    )
            except asyncio.CancelledError:
                if entry is None or not entry.cancel_requested:
                    # Disconnects and debug timeouts keep their existing owner.
                    raise
                workflow_events.emit_workflow_failed("任务已停止", "agent_execution")
            except ValueError as exc:
                workflow_events.emit_workflow_failed(str(exc), "agent_resolution")
            except NotFoundError as exc:
                workflow_events.emit_workflow_failed(str(exc), "agent_resolution")
            except Exception as exc:  # pragma: no cover - streaming error path
                self._logger.exception(
                    "Agent workflow failed during streaming",
                    request_id=request_id,
                    correlation_id=correlation_id,
                )
                workflow_events.emit_workflow_failed(str(exc), "agent_execution")
            finally:
                if execution_id is not None:
                    await self._unregister_run(execution_id)

        async def bounded_device_task() -> None:
            try:
                await asyncio.wait_for(coordination_task(), timeout=payload.timeout)
            except asyncio.TimeoutError:
                workflow_events.emit_workflow_failed(
                    "调试超时，已停止后续操作，请确认设备状态", "agent_execution"
                )

        task = asyncio.create_task(
            bounded_device_task()
            if payload.expected_device_id is not None
            else coordination_task()
        )

        async def on_finished() -> None:
            await report_phase_ended(payload.reply_phase)

        try:
            response = create_sse_response(event_emitter, http_request)
        except BaseException:
            await finish_execution(task, on_finished)
            raise
        if payload.expected_device_id is not None or payload.cancel_on_disconnect:
            return own_execution(response, task, on_finished=on_finished)
        return response

    async def cancel(
        self, run_id: str, project_id: uuid.UUID, reason: Optional[str] = None
    ) -> bool:
        """Acknowledge a stop signal, not completion of cleanup or remote actions."""
        async with self._runs_lock:
            entry = self._runs.get(run_id)
            if entry is None or str(project_id) != entry.project_id:
                return False
            if entry.cancel_requested:
                return True
            if (
                not entry.accepting_cancel
                or entry.task.done()
                or entry.task.cancelling()
            ):
                return False
            if not entry.task.cancel():
                return False
            entry.cancel_requested = True
            entry.accepting_cancel = False
            if entry.device_execution is not None:
                # Captured tool closures are fenced before the owner resumes.
                entry.device_execution.closed = True
            return True

    async def _register_run(self, run_id: str, entry: RunRegistryEntry) -> None:
        async with self._runs_lock:
            self._runs[run_id] = entry
            self._logger.debug(
                "Registered running agent execution",
                run_id=run_id,
                agent_id=entry.agent_id,
                request_id=entry.request_id,
            )

    async def _unregister_run(self, run_id: str) -> None:
        async with self._runs_lock:
            entry = self._runs.pop(run_id, None)
        if entry is not None:
            self._logger.debug(
                "Unregistered agent execution",
                run_id=run_id,
                agent_id=entry.agent_id,
                request_id=entry.request_id,
            )

    async def _prepare_context(
        self,
        payload: SupervisorRunRequest,
        project_id: uuid.UUID,
        headers: Dict[str, str],
    ) -> Tuple[AgentExecutionContext, str]:
        if payload.knowledge_only and any(
            key in headers for key in (
                "X-Business-Request-Context", "X-Business-Request-ID",
                "X-Workflow-Parent-Execution-ID", "X-Workflow-Lineage",
            )
        ):
            raise ValueError("Knowledge-only run cannot carry business or workflow authority")
        principal = None
        business_token = None
        if "X-Business-Request-Context" in headers or "X-Business-Request-ID" in headers:
            try:
                visitor_id = uuid.UUID(payload.user_id or "")
                check = BusinessContextCheck(project_id=project_id, visitor_id=visitor_id,
                    channel=KnowledgeChannel(headers["X-Execution-Channel"]),
                    request_id=uuid.UUID(headers["X-Business-Request-ID"]),
                    request_fingerprint=business_request_fingerprint(visitor_id, payload.message),
                    context_token=SecretStr(headers["X-Business-Request-Context"]))
            except (KeyError, ValueError):
                raise ValueError("当前客户业务会话范围无法确认") from None
            principal = await api_service_client.get_business_request_context(check)
            business_token = check.context_token
        async with self._agent_service_context() as agent_service:
            async with AIServiceClient(agent_service, project_id) as ai_client:
                if payload.agent_id:
                    agent = await ai_client.get_agent(str(payload.agent_id), headers)
                else:
                    agent = await ai_client.get_default_agent(headers)
            agent = await self._authorize_execution(agent_service.db, project_id, agent, headers)
            reference = await self._reference_for_execution(agent_service.db, project_id, agent, headers)
            agent_id = str(agent.id)

        if payload.knowledge_only and agent.is_remote_store_agent:
            raise ValueError("Remote agent cannot provide isolated knowledge generation")

        if ("X-Workflow-Parent-Execution-ID" in headers
                and agent.bound_device_id is not None
                and (payload.expected_device_id is None
                     or payload.expected_device_policy_digest is None)):
            raise ValueError("设备员工工作流必须固定设备读取策略后才能执行")

        if payload.expected_device_id is not None:
            if agent.bound_device_id != str(payload.expected_device_id):
                raise ValueError("设备绑定已变化，请重新选择 AI 员工后再调试")
        if payload.expected_device_policy_digest is not None:
            if agent.bound_device_policy_digest != payload.expected_device_policy_digest:
                raise ValueError("设备读取策略已变化，请重新核对工作流节点配置")

        rag_url = payload.rag_url
        if payload.require_current_knowledge and not rag_url:
            rag_url = settings.rag_service_url

        context = AgentExecutionContext(
            agent=agent,
            execution_reference=reference,
            business_principal=principal,
            business_context_token=business_token,
            parent_workflow_execution_id=(
                uuid.UUID(headers["X-Workflow-Parent-Execution-ID"])
                if "X-Workflow-Parent-Execution-ID" in headers else None
            ),
            workflow_lineage=(
                tuple(json.loads(headers["X-Workflow-Lineage"]))
                if "X-Workflow-Lineage" in headers else ()
            ),
            project_id=str(project_id),
            message=payload.message,
            system_message=payload.system_message,
            expected_output=payload.expected_output,
            session_id=payload.session_id,
            user_id=payload.user_id,
            request_id=headers["X-Request-ID"],
            timeout=payload.timeout,
            mcp_url=payload.mcp_url,
            rag_url=rag_url,
            knowledge_channel=payload.knowledge_channel,
            enable_memory=payload.enable_memory,
            disable_tools=payload.disable_tools,
            response_purpose=payload.response_purpose,
            require_current_knowledge=payload.require_current_knowledge,
            knowledge_context=payload.knowledge_context,
            markdown=payload.markdown,
            temperature=payload.temperature,
            excluded_tool_ids=payload.excluded_tool_ids,
            ui_mode=payload.ui_mode,
        )
        if (
            context.response_purpose != "expression"
            and not context.disable_tools
            and context.rag_url
            and context.knowledge_channel is not None
            and not (
                context.require_current_knowledge
                and SOCIAL_ONLY.fullmatch(context.message)
            )
        ):
            try:
                context.agent = await resolve_agent_knowledge(
                    context.agent, context.knowledge_channel
                )
            except Exception as exc:
                if not context.require_current_knowledge:
                    raise
                self._logger.warning(
                    "Current knowledge scope unavailable",
                    error_type=type(exc).__name__,
                )
                context.knowledge_evidence = KnowledgeEvidence(
                    status="unavailable",
                    retrieved_at=datetime.now(timezone.utc),
                    project_id=context.project_id,
                    channel=context.knowledge_channel.value,
                )
        if payload.knowledge_only:
            if context.knowledge_channel is None:
                raise ValueError("Knowledge-only channel is required")
            evidence = context.knowledge_evidence or await retrieve_current_knowledge(context)
            if (
                evidence.project_id != context.project_id
                or evidence.channel != context.knowledge_channel.value
                or evidence.tool_results
            ):
                raise ValueError("Knowledge-only evidence ownership mismatch")
            context.knowledge_evidence = evidence
            context.agent = context.agent.model_copy(deep=True, update={
                "tools": [], "workflows": [], "business_connections": [],
                "bound_device_id": None, "bound_device_policy_digest": None,
                "skills_enabled": False,
            })
            context.disable_tools = True
            context.enable_memory = False
            context.session_id = None
            context.user_id = None
            context.execution_reference = None
            context.business_principal = None
            context.business_context_token = None
        return context, agent_id

    async def _reference_for_execution(self, db: AsyncSession, project_id: uuid.UUID,
                                       agent: Agent, headers: Dict[str, str]) -> AgentExecutionReference:
        from app.schemas.knowledge import KnowledgeChannel
        from app.services.agent_execution_reference import reference_for
        raw = headers.get("X-Execution-Channel")
        return await reference_for(db, project_id, agent, KnowledgeChannel(raw) if raw is not None else None)

    async def _authorize_execution(
        self, db: AsyncSession, project_id: uuid.UUID, agent: Agent, headers: Dict[str, str],
    ) -> Agent:
        from app.schemas.knowledge import KnowledgeChannel
        from app.services.agent_execution_guard import AgentExecutionGuard
        raw = headers.get("X-Execution-Channel")
        channel = KnowledgeChannel(raw) if raw is not None else None
        return await AgentExecutionGuard(db).prepare(project_id, agent, channel)

    @asynccontextmanager
    async def _agent_service_context(self) -> AsyncIterator[AgentService]:
        session: AsyncSession = self._session_factory()
        try:
            yield AgentService(session)
        except Exception:
            if session.in_transaction():
                await session.rollback()
            raise
        else:
            if session.in_transaction():
                await session.rollback()
        finally:
            await session.close()

    async def _resolve_agent_id(
        self,
        payload: SupervisorRunRequest,
        project_id: uuid.UUID,
        agent_service: AgentService,
    ) -> str:
        if payload.agent_id:
            return str(payload.agent_id)

        try:
            default_agent = await agent_service.get_default_agent(project_id)
        except NotFoundError as exc:
            raise ValueError("Default agent not configured for project") from exc
        return str(default_agent.id)

    @staticmethod
    def _build_auth_headers(
        project_id: uuid.UUID, extra_headers: Optional[Dict[str, str]]
    ) -> Dict[str, str]:
        headers = dict(extra_headers or {})
        headers.setdefault("X-Project-ID", str(project_id))
        headers.setdefault("X-Request-ID", str(uuid.uuid4()))
        return headers

    @staticmethod
    def _build_failure_response(
        message: str, execution_id: uuid.UUID | None = None,
    ) -> SupervisorRunResponse:
        return SupervisorRunResponse(
            success=False,
            execution_id=execution_id,
            message=message,
            result=None,
            content="",
            metadata=None,
            error=message,
        )
