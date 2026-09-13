"""Supervisor runtime service implemented via direct single-agent execution."""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Dict, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request
from starlette.responses import StreamingResponse

from app.core.logging import get_logger
from app.exceptions import NotFoundError
from app.models.internal import AgentExecutionContext
from app.runtime.supervisor.agents.builder import AgnoAgentBuilder
from app.runtime.supervisor.agents.runner import AgnoAgentRunner
from app.runtime.supervisor.infrastructure.services import AIServiceClient
from app.runtime.supervisor.streaming.workflow_events import create_workflow_events
from app.runtime.tools.executor.service import ToolsRuntimeService
from app.runtime.tools.mcp_lifecycle import mcp_request_scope
from app.schemas.agent_run import SupervisorRunRequest, SupervisorRunResponse
from app.services.agent_service import AgentService
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

        try:
            context, _ = await self._prepare_context(payload, project_id, headers)
            async with track_device_execution(context) as execution, mcp_request_scope():
                built_agent = await self._agent_builder.build_agent(context)
                self._logger.debug(
                    "Starting supervisor run",
                    agent_id=str(context.agent.id),
                    request_id=context.request_id,
                )
                result = await self._agent_runner.run(built_agent, context)
                if execution is not None:
                    execution.complete(result.success)
                return result
        except NotFoundError as exc:
            return self._build_failure_response(str(exc))
        except ValueError as exc:
            return self._build_failure_response(str(exc))
        except Exception as exc:  # pragma: no cover - defensive runtime path
            self._logger.exception(
                "Supervisor run failed",
                project_id=str(project_id),
                request_id=headers.get("X-Request-ID"),
            )
            return self._build_failure_response(str(exc) or "Agent run failed")

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
                workflow_events.emit_workflow_started(request_id, context)
                async with track_device_execution(context) as execution, mcp_request_scope():
                    built_agent = await self._agent_builder.build_agent(context)
                    execution_id = str(
                        execution.identity.session_id if execution else uuid.uuid4()
                    )

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
                        agent_result = await self._agent_runner.stream(
                            built_agent,
                            context,
                            workflow_events,
                            execution_id,
                        )
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
        async with self._agent_service_context() as agent_service:
            async with AIServiceClient(agent_service, project_id) as ai_client:
                if payload.agent_id:
                    agent = await ai_client.get_agent(str(payload.agent_id), headers)
                else:
                    agent = await ai_client.get_default_agent(headers)
            agent_id = str(agent.id)

        if payload.expected_device_id is not None:
            if agent.bound_device_id != str(payload.expected_device_id):
                raise ValueError("设备绑定已变化，请重新选择 AI 员工后再调试")

        context = AgentExecutionContext(
            agent=agent,
            project_id=str(project_id),
            message=payload.message,
            system_message=payload.system_message,
            expected_output=payload.expected_output,
            session_id=payload.session_id,
            user_id=payload.user_id,
            request_id=headers["X-Request-ID"],
            timeout=payload.timeout,
            mcp_url=payload.mcp_url,
            rag_url=payload.rag_url,
            knowledge_channel=payload.knowledge_channel,
            enable_memory=payload.enable_memory,
            disable_tools=payload.disable_tools,
            response_purpose=payload.response_purpose,
            markdown=payload.markdown,
            temperature=payload.temperature,
            excluded_tool_ids=payload.excluded_tool_ids,
            ui_mode=payload.ui_mode,
        )
        return context, agent_id

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
    def _build_failure_response(message: str) -> SupervisorRunResponse:
        return SupervisorRunResponse(
            success=False,
            message=message,
            result=None,
            content="",
            metadata=None,
            error=message,
        )
