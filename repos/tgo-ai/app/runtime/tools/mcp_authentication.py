"""Preserve MCP tool definitions while surfacing required authentication."""

from __future__ import annotations

from functools import wraps
from inspect import isawaitable

from agno.tools import Function
from mcp import McpError


def _find_mcp_error(exc: BaseException) -> McpError | None:
    if isinstance(exc, McpError):
        return exc
    if isinstance(exc, ExceptionGroup):
        for nested in exc.exceptions:
            found = _find_mcp_error(nested)
            if found is not None:
                return found
    return None


def _interaction_message(data: object) -> str:
    if not isinstance(data, dict):
        return "Interaction required"
    message: object = data.get("message")
    if isinstance(message, dict):
        message = message.get("text")
    text = message if isinstance(message, str) and message else (
        "Interaction required"
    )
    url: object = data.get("url")
    return f"{text} {url}" if isinstance(url, str) and url else text


def wrap_mcp_authenticate_tool(func: Function) -> Function:
    """Keep schema and execution policy; change only the callable boundary."""
    original = func.entrypoint
    if original is None:
        raise ValueError("MCP tool entrypoint is required")

    @wraps(original)
    async def wrapped(*args: object, **kwargs: object) -> object:
        try:
            result: object = original(*args, **kwargs)
            if isawaitable(result):
                result = await result
            return result
        except BaseException as exc:
            error = _find_mcp_error(exc)
            if error is not None and error.error.code == -32003:
                raise RuntimeError(
                    _interaction_message(error.error.data),
                ) from exc
            raise

    # Rebuilding from **kwargs loses required parameters, hooks and approval
    # flags. Copy the existing Function without mutating its shared original.
    wrapped_function = func.model_copy()
    wrapped_function.entrypoint = wrapped
    return wrapped_function
