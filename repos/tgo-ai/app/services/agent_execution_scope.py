"Every captured tool and outgoing result remains bound to the exact \
owned run."
import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import cast

from agno.exceptions import StopAgentRun
from pydantic import JsonValue

from app.schemas.agent_execution import AgentExecutionReference

ReferenceReader = Callable[
    [AgentExecutionReference], Awaitable[AgentExecutionReference]
]
REVOKED_MESSAGE = "本次执行的授权或配置已变化，答复暂停发送，请重新核验或转人工"


class ExecutionRevoked(StopAgentRun):
    def __init__(self) -> None:
        super().__init__(REVOKED_MESSAGE)


class AgentExecutionScope:
    def __init__(
        self, reference: AgentExecutionReference, reader: ReferenceReader
    ):
        self.reference = reference
        self.reader = reader
        self.owner = cast(asyncio.Task[object] | None, asyncio.current_task())
        self.closed = False
        self.revoked = False
        self.owned_cancel = False
        self.lock = asyncio.Lock()

    def _revoke(self) -> None:
        was_closed = self.closed
        self.closed = self.revoked = True
        # Agno gathers tool tasks with return_exceptions=True. Cancel the
        # actual
        # model owner so a swallowed tool error cannot start another model
        # call.
        if (
            not was_closed
            and self.owner is not None
            and self.owner is not asyncio.current_task()
            and not self.owner.done()
        ):
            if not self.owned_cancel:
                self.owned_cancel = self.owner.cancel()

    def release_owned_cancel(self) -> None:
        owner = self.owner
        if (
            self.owned_cancel
            and owner is not None
            and owner is asyncio.current_task()
            and owner.cancelling() == 1
        ):
            owner.uncancel()
        self.owned_cancel = False

    def revoke(self) -> None:
        (
            'Stop this owner when a trusted tool cannot verify its original '
            'grant.'
        )
        self._revoke()

    async def check(self) -> None:
        if self.closed:
            raise ExecutionRevoked()
        async with self.lock:
            if self.closed:
                raise ExecutionRevoked()
            try:
                current = await self.reader(self.reference)
                if self.closed or current != self.reference:
                    raise ValueError("Execution changed")
            except Exception:
                self._revoke()
                raise ExecutionRevoked() from None

    async def tool_hook(
        self,
        function_call: Callable[..., object],
        arguments: dict[str, JsonValue],
    ) -> object:
        await self.check()
        value = function_call(**arguments)
        if inspect.isawaitable(value):
            value = await cast(Awaitable[object], value)
        await self.check()
        if isinstance(value, AsyncIterator):
            return self._guarded_async(value)
        if isinstance(value, Iterator):
            return self._guarded_sync(value)
        return value

    async def _guarded_async(
        self, iterator: AsyncIterator[object]
    ) -> AsyncIterator[object]:
        try:
            while True:
                await self.check()
                try:
                    value = await anext(iterator)
                except StopAsyncIteration:
                    return
                await self.check()
                yield value
        finally:
            close = getattr(iterator, "aclose", None)
            if callable(close):
                await close()

    async def _guarded_sync(
        self, iterator: Iterator[object]
    ) -> AsyncIterator[object]:
        def next_item() -> tuple[bool, object]:
            try:
                return True, next(iterator)
            except StopIteration:
                return False, None

        try:
            while True:
                await self.check()
                task = asyncio.create_task(asyncio.to_thread(next_item))
                try:
                    present, value = await asyncio.shield(task)
                except asyncio.CancelledError:
                    # A running synchronous step cannot be force-cancelled.
                    # Await its cleanup before acknowledging an owned stop.
                    await task
                    raise
                if not present:
                    return
                await self.check()
                yield value
        finally:
            close = getattr(iterator, "close", None)
            if callable(close):
                close()


_scope: ContextVar[AgentExecutionScope | None] = ContextVar(
    "agent_execution_scope", default=None
)


def current_execution_scope() -> AgentExecutionScope | None:
    return _scope.get()


async def check_current_execution() -> None:
    scope = _scope.get()
    if scope is not None:
        await scope.check()


@asynccontextmanager
async def track_execution_scope(
    reference: AgentExecutionReference | None, reader: ReferenceReader
) -> AsyncIterator[AgentExecutionScope | None]:
    if reference is None:
        yield _scope.get()
        return
    scope = AgentExecutionScope(reference, reader)
    token = _scope.set(scope)
    try:
        yield scope
    except asyncio.CancelledError:
        if scope.revoked:
            scope.release_owned_cancel()
            raise ExecutionRevoked() from None
        raise
    finally:
        scope.closed = True
        _scope.reset(token)
