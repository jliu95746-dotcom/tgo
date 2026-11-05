"""Continue one remote run only after a complete, matching pause request."""

from __future__ import annotations

from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Coroutine,
    Sequence,
)
from dataclasses import replace
from inspect import isawaitable
from typing import cast

from agno.models.response import ToolExecution
from agno.exceptions import StopAgentRun
from agno.run import RunStatus
from agno.run.agent import (
    BaseAgentRunEvent,
    RunCancelledEvent,
    RunCompletedEvent,
    RunErrorEvent,
    RunOutput,
    RunOutputEvent,
    RunPausedEvent,
)

from app.runtime.tools.remote_tool_execution import (
    RemoteToolError,
    is_cancellation,
    pending_tools,
)
from app.services.agent_execution_scope import check_current_execution

ExecuteTools = Callable[
    [Sequence[ToolExecution]], Awaitable[list[ToolExecution]]
]
TerminalEvent = (
    RunCompletedEvent | RunPausedEvent | RunErrorEvent | RunCancelledEvent
)


class RemoteRun:
    def __init__(
        self,
        execute: ExecuteTools,
        resume: Callable[..., object],
        identifier: str | None,
        name: str | None,
        session_id: str | None,
        user_id: str | None = None,
        can_resume: bool = True,
    ) -> None:
        self.execute = execute
        self.resume = resume
        self.identifier = identifier
        self.name = name
        self.session_id = session_id
        self.user_id = user_id
        self.can_resume = can_resume
        self._run_id: str | None = None
        self._seen: set[str] = set()
        self._rounds = 0

    def _context(self, run_id: str | None, session_id: str | None) -> None:
        if session_id is not None:
            if not isinstance(session_id, str):
                raise RemoteToolError()
            if self.session_id is not None and session_id != self.session_id:
                raise RemoteToolError()
            self.session_id = session_id
        if run_id is not None:
            if (
                not isinstance(run_id, str)
                or not run_id.strip()
                or len(run_id) > 512
                or run_id in {".", ".."}
                or any(
                    char in "/\\%?#" or char.isspace() or ord(char) < 32
                    for char in run_id
                )
            ):
                raise RemoteToolError()
            if self._run_id is not None and self._run_id != run_id:
                raise RemoteToolError()
            self._run_id = run_id

    def _batch(self, calls: Sequence[ToolExecution]) -> list[ToolExecution]:
        prepared = pending_tools(calls)
        if not self.can_resume or not self._run_id or self._rounds >= 10:
            raise RemoteToolError()
        identifiers = {item.tool_call_id for item in prepared}
        if identifiers & self._seen or None in identifiers:
            raise RemoteToolError()
        self._seen.update(
            identifier for identifier in identifiers if identifier is not None
        )
        self._rounds += 1
        return prepared

    async def _execute(
        self, prepared: list[ToolExecution]
    ) -> list[ToolExecution]:
        updated = await self.execute(prepared)
        await check_current_execution()
        if (
            [item.tool_call_id for item in updated]
            != [item.tool_call_id for item in prepared]
            or any(
                item.external_execution_required is not False
                for item in updated
            )
            or any(item.stop_after_tool_call for item in updated)
        ):
            # A tool may already have run: never claim rollback or re-run it.
            raise RemoteToolError()
        return updated

    def _output(self, value: RunOutput) -> RunOutput:
        return replace(
            value,
            agent_id=self.identifier or value.agent_id,
            agent_name=self.name or value.agent_name,
            session_id=self.session_id or value.session_id,
        )

    def _event(self, event: RunOutputEvent) -> RunOutputEvent:
        if isinstance(event, RunErrorEvent):
            event = replace(
                event,
                content="远程员工执行失败，请重试或转人工处理",
                error_type="remote_error",
                error_id=None,
                additional_data=None,
            )
        elif isinstance(event, RunCancelledEvent):
            event = replace(event, reason="远程员工执行已取消")
        return replace(
            event,
            agent_id=self.identifier or event.agent_id,
            agent_name=self.name or event.agent_name,
            session_id=self.session_id or event.session_id,
        )

    async def complete(self, first: Awaitable[RunOutput]) -> RunOutput:
        try:
            await check_current_execution()
            current = await first
            while True:
                await check_current_execution()
                if not isinstance(current, RunOutput):
                    raise RemoteToolError()
                self._context(current.run_id, current.session_id)
                if (
                    current.user_id is not None
                    and self.user_id is not None
                    and current.user_id != self.user_id
                ):
                    raise RemoteToolError()
                pending = pending_tools(current.tools or [])
                if not pending:
                    if current.status not in {
                        RunStatus.completed,
                        RunStatus.paused,
                        RunStatus.cancelled,
                        RunStatus.error,
                    }:
                        raise RemoteToolError()
                    return self._output(current)
                if current.status != RunStatus.paused or not current.run_id:
                    raise RemoteToolError()
                updated = await self._execute(self._batch(pending))
                continued = self.resume(
                    run_id=current.run_id,
                    updated_tools=updated,
                    session_id=self.session_id,
                    stream=False,
                )
                if not isawaitable(continued):
                    raise RemoteToolError()
                value: object = await continued
                if not isinstance(value, RunOutput):
                    raise RemoteToolError()
                current = value
        except StopAgentRun:
            raise
        except Exception as exc:
            if is_cancellation(exc):
                raise
            raise RemoteToolError() from None
        finally:
            if isinstance(first, Coroutine):
                first.close()

    async def stream(
        self, first: AsyncIterator[RunOutputEvent]
    ) -> AsyncGenerator[RunOutputEvent, None]:
        current = first
        try:
            await check_current_execution()
            while True:
                terminal: TerminalEvent | None = None
                try:
                    async for event in current:
                        if terminal is not None or not isinstance(
                            event, BaseAgentRunEvent
                        ):
                            raise RemoteToolError()
                        if isinstance(
                            event,
                            (
                                RunCompletedEvent,
                                RunPausedEvent,
                                RunErrorEvent,
                                RunCancelledEvent,
                            ),
                        ):
                            terminal = event
                        else:
                            yield self._event(event)
                finally:
                    if isinstance(current, AsyncGenerator):
                        await current.aclose()
                if terminal is None:
                    raise RemoteToolError()
                await check_current_execution()
                self._context(terminal.run_id, terminal.session_id)
                if isinstance(terminal, RunCompletedEvent) and pending_tools(
                    terminal.tools or []
                ):
                    raise RemoteToolError()
                if not isinstance(terminal, RunPausedEvent):
                    yield self._event(terminal)
                    return
                pending = pending_tools(terminal.tools or [])
                if not pending:
                    yield self._event(terminal)
                    return
                updated = await self._execute(self._batch(pending))
                continued = self.resume(
                    run_id=terminal.run_id,
                    updated_tools=updated,
                    session_id=self.session_id,
                    stream=True,
                )
                if isawaitable(continued):
                    continued = await continued
                if not isinstance(continued, AsyncIterator):
                    raise RemoteToolError()
                # The SDK returns this exact event union. Each item is checked
                # against its base event class before being forwarded.
                current = cast(AsyncIterator[RunOutputEvent], continued)
        except StopAgentRun:
            raise
        except Exception as exc:
            if is_cancellation(exc):
                raise
            raise RemoteToolError() from None
        finally:
            if isinstance(current, AsyncGenerator):
                await current.aclose()
