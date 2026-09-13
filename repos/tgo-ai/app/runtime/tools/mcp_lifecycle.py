"""Release preconnected MCP resources in their owning request task."""

from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from typing import AsyncIterator, Protocol

from app.core.logging import get_logger
from app.runtime.core.exceptions import MCPToolError


class MCPConnection(Protocol):
    async def close(self) -> None: ...


_connections: ContextVar[AsyncExitStack | None] = ContextVar("mcp_request_connections", default=None)
_logger = get_logger(__name__)


async def _close_connection(connection: MCPConnection) -> None:
    try:
        await connection.close()
    except Exception as exc:
        _logger.warning("MCP connection cleanup failed", error_type=type(exc).__name__)
        raise MCPToolError("工具连接清理失败，请稍后重试。") from None


def own_mcp_connection(connection: MCPConnection) -> None:
    """Manual low-level callers without a scope still own their own cleanup."""
    stack = _connections.get()
    if stack is not None:
        stack.push_async_callback(_close_connection, connection)


@asynccontextmanager
async def mcp_request_scope() -> AsyncIterator[None]:
    """Close in reverse order, even on build failure or cancellation.

    Cleanup must run in the same task as connect: MCP transports contain
    nested AnyIO cancel scopes and cannot be handed off to a cleanup task.
    """
    stack = AsyncExitStack()
    token = _connections.set(stack)
    try:
        async with stack:
            yield
    finally:
        _connections.reset(token)
