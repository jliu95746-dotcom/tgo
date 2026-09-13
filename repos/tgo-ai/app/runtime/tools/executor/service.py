"""工具智能体运行时服务."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import aclosing
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from agno.agent import (
    RunCancelledEvent,
    RunCompletedEvent,
    RunContentEvent,
    RunErrorEvent,
    RunOutput,
    ToolCallCompletedEvent,
    ToolCallStartedEvent,
)
from app.config import settings
from app.core.logging import get_logger
from app.runtime.tools.builder.agent_builder import AgentBuilder
from app.runtime.tools.config import ToolsRuntimeSettings
from app.runtime.tools.mcp_lifecycle import mcp_request_scope
from app.runtime.tools.models import (
    AgentRunRequest,
    AgentRunResponse,
    CompleteStreamEvent,
    ContentStreamEvent,
    ErrorStreamEvent,
    StreamEventType,
    ToolCallStreamEvent,
    ToolExecution,
)
from app.runtime.core.exceptions import (
    AgentExecutionError,
    StreamingError,
    InvalidConfigurationError,
    MissingConfigurationError,
)


class ToolsRuntimeService:
    """封装工具智能体执行逻辑."""

    def __init__(self, runtime_settings: ToolsRuntimeSettings | None = None) -> None:
        self._settings = runtime_settings or settings.tools_runtime
        self._builder = AgentBuilder(self._settings)
        self._logger = get_logger(__name__)

    async def run_agent(self, request: AgentRunRequest) -> AgentRunResponse:
        async with mcp_request_scope():
            return await self._run_agent(request)

    async def _run_agent(self, request: AgentRunRequest) -> AgentRunResponse:
        """执行非流式智能体调用.

        Args:
            request: Agent run request with message and configuration

        Returns:
            AgentRunResponse with content and tool executions

        Raises:
            InvalidConfigurationError: If agent configuration is invalid
            MissingConfigurationError: If required configuration is missing
            AgentExecutionError: If agent execution fails
        """
        # Validate message
        if not request.message:
            self._logger.warning("Agent run request missing message")
            return AgentRunResponse(success=False, error="message is required")

        # Build agent
        try:
            agent = await self._builder.build_agent(request)
        except (InvalidConfigurationError, MissingConfigurationError) as e:
            self._logger.error(
                "Failed to build agent due to configuration error",
                error=str(e),
                error_type=type(e).__name__,
                session_id=request.session_id,
                user_id=request.user_id
            )
            return AgentRunResponse(
                success=False,
                error=str(e),
                metadata={"error_type": type(e).__name__},
            )
        except Exception as e:
            self._logger.error(
                "Unexpected error building agent",
                error=str(e),
                error_type=type(e).__name__,
                session_id=request.session_id,
                user_id=request.user_id,
                exc_info=True
            )
            return AgentRunResponse(
                success=False,
                error=f"Failed to build agent: {str(e)}",
                metadata={"error_type": type(e).__name__},
            )

        # Execute agent
        try:
            self._logger.debug(
                "Starting agent execution",
                session_id=request.session_id,
                user_id=request.user_id,
                message_length=len(request.message)
            )

            result: RunOutput = await agent.arun(
                request.message,
                stream=False,
                session_id=request.session_id,
                user_id=request.user_id,
            )

            self._logger.debug(
                "Agent execution completed",
                session_id=request.session_id,
                user_id=request.user_id,
                has_content=bool(result.content)
            )
        except Exception as exc:
            self._logger.error(
                "Agent execution failed",
                error=str(exc),
                error_type=type(exc).__name__,
                session_id=request.session_id,
                user_id=request.user_id,
                exc_info=True
            )
            raise AgentExecutionError(
                "Agent execution failed",
                session_id=request.session_id,
                user_id=request.user_id,
                error=str(exc)
            ) from exc

        # Extract tool executions
        tool_records = []
        for tool_call in getattr(result, "tools", []) or []:
            tool_records.append(
                ToolExecution(
                    tool_call_id=tool_call.tool_call_id,
                    tool_name=tool_call.tool_name,
                    tool_args=tool_call.tool_args,
                    tool_call_error=tool_call.tool_call_error,
                    result=tool_call.result,
                )
            )

        return AgentRunResponse(
            content=result.content,
            tools=tool_records or None,
            success=True,
            metadata=None,
        )

    async def stream_agent(self, request: AgentRunRequest) -> AsyncIterator[StreamEventType]:
        async with mcp_request_scope():
            async with aclosing(self._stream_agent(request)) as stream:
                async for event in stream:
                    yield event

    async def _stream_agent(self, request: AgentRunRequest) -> AsyncGenerator[StreamEventType, None]:
        """执行流式智能体调用, 返回事件流.

        Args:
            request: Agent run request with message and configuration

        Yields:
            StreamEventType: Stream events (content, tool calls, errors, completion)

        Raises:
            InvalidConfigurationError: If agent configuration is invalid
            MissingConfigurationError: If required configuration is missing
            StreamingError: If streaming fails
        """
        # Validate message
        if not request.message:
            self._logger.warning("Agent stream request missing message")
            yield ErrorStreamEvent(
                event="error",
                error="message is required",
                error_type="ValueError",
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            return

        # Build agent
        try:
            agent = await self._builder.build_agent(request)
        except (InvalidConfigurationError, MissingConfigurationError) as e:
            self._logger.error(
                "Failed to build agent for streaming due to configuration error",
                error=str(e),
                error_type=type(e).__name__,
                session_id=request.session_id,
                user_id=request.user_id
            )
            yield ErrorStreamEvent(
                error=str(e),
                error_type=type(e).__name__,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            return
        except Exception as e:
            self._logger.error(
                "Unexpected error building agent for streaming",
                error=str(e),
                error_type=type(e).__name__,
                session_id=request.session_id,
                user_id=request.user_id,
                exc_info=True
            )
            yield ErrorStreamEvent(
                error=f"Failed to build agent: {str(e)}",
                error_type=type(e).__name__,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            return

        # Start streaming
        try:
            self._logger.debug(
                "Starting agent streaming",
                session_id=request.session_id,
                user_id=request.user_id,
                message_length=len(request.message),
                stream_intermediate_steps=request.stream_intermediate_steps
            )

            response_stream = agent.arun(
                request.message,
                stream=True,
                # Completion/error events are required even when intermediate
                # tool steps are hidden from the caller.
                stream_events=True,
                session_id=request.session_id,
                user_id=request.user_id,
            )
        except Exception as exc:
            self._logger.error(
                "Failed to start agent streaming",
                error=str(exc),
                error_type=type(exc).__name__,
                session_id=request.session_id,
                user_id=request.user_id,
                exc_info=True
            )
            raise StreamingError(
                "Failed to start agent streaming",
                session_id=request.session_id,
                user_id=request.user_id,
                error=str(exc)
            ) from exc

        # Process stream events
        terminal_seen = False
        tool_records: list[ToolExecution] = []
        try:
            async for event in response_stream:
                try:
                    converted = self._convert_agno_event(event)
                    if terminal_seen:
                        continue
                    if isinstance(converted, ToolCallStreamEvent):
                        if converted.status == "completed":
                            tool_records.append(ToolExecution(
                                tool_call_id=converted.tool_call_id,
                                tool_name=converted.tool_name,
                                tool_args=converted.tool_input,
                                result=converted.tool_output,
                                tool_call_error=converted.tool_call_error,
                            ))
                        if not request.stream_intermediate_steps:
                            continue
                    if isinstance(converted, CompleteStreamEvent):
                        terminal_seen = True
                        if converted.final_response is not None:
                            converted.final_response.tools = tool_records or None
                    elif isinstance(converted, ErrorStreamEvent):
                        terminal_seen = True
                    if converted:
                        yield converted
                except Exception as e:
                    # Log conversion error but continue streaming
                    self._logger.warning(
                        "Failed to convert stream event, skipping",
                        event_type=type(event).__name__,
                        error=str(e),
                        error_type=type(e).__name__,
                        session_id=request.session_id
                    )
                    continue
        except Exception as exc:
            self._logger.error(
                "Error during stream processing",
                error=str(exc),
                error_type=type(exc).__name__,
                session_id=request.session_id,
                user_id=request.user_id,
                exc_info=True
            )
            yield ErrorStreamEvent(
                error=f"Stream processing error: {str(exc)}",
                error_type=type(exc).__name__,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            raise StreamingError(
                "Error during stream processing",
                session_id=request.session_id,
                user_id=request.user_id,
                error=str(exc)
            ) from exc
        finally:
            # Finish the model generator before releasing its MCP connections.
            if isinstance(response_stream, AsyncGenerator):
                await response_stream.aclose()

        if not terminal_seen:
            yield ErrorStreamEvent(
                error="Agent stream ended without a completion event",
                error_type="IncompleteStream",
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        self._logger.debug(
            "Agent streaming completed",
            session_id=request.session_id,
            user_id=request.user_id
        )

    def _convert_agno_event(self, agno_event: Any) -> StreamEventType | None:
        """Convert Agno agent event to stream event.

        Args:
            agno_event: Event from Agno agent stream

        Returns:
            Converted stream event or None if event type is not handled
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        if isinstance(agno_event, RunContentEvent):
            content = agno_event.content or ""
            return ContentStreamEvent(content=content, timestamp=timestamp)

        if isinstance(agno_event, RunCompletedEvent):
            content = agno_event.content or ""
            final_response = AgentRunResponse(content=content, success=True)
            return CompleteStreamEvent(final_response=final_response, timestamp=timestamp)

        if isinstance(agno_event, RunErrorEvent):
            return ErrorStreamEvent(
                error=agno_event.content or "Agent run failed",
                error_type=agno_event.error_type or "RunErrorEvent",
                timestamp=timestamp,
            )

        if isinstance(agno_event, RunCancelledEvent):
            return ErrorStreamEvent(
                error=agno_event.reason or "Agent run cancelled",
                error_type="RunCancelledEvent",
                timestamp=timestamp,
            )

        if isinstance(agno_event, ToolCallStartedEvent) and agno_event.tool:
            tool = agno_event.tool
            return ToolCallStreamEvent(
                tool_call_id=getattr(tool, "tool_call_id", None),
                tool_name=getattr(tool, "tool_name", "unknown_tool"),
                tool_input=getattr(tool, "tool_args", None),
                status="started",
                timestamp=timestamp,
            )

        if isinstance(agno_event, ToolCallCompletedEvent) and agno_event.tool:
            tool = agno_event.tool
            return ToolCallStreamEvent(
                tool_call_id=getattr(tool, "tool_call_id", None),
                tool_name=getattr(tool, "tool_name", "unknown_tool"),
                tool_input=getattr(tool, "tool_args", None),
                tool_output=getattr(tool, "result", None),
                tool_call_error=getattr(tool, "tool_call_error", False),
                status="completed",
                timestamp=timestamp,
            )

        return None
