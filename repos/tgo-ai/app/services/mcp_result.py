"""Preserve MCP tool failure semantics and structured output."""

import json

from mcp.types import CallToolResult, TextContent


class MCPToolResultError(ValueError):
    """The server completed the protocol request but the tool itself failed."""


def mcp_result_text(result: CallToolResult) -> str:
    """Return complete usable output, never turning an MCP error into success."""
    text = "\n".join(block.text for block in result.content if isinstance(block, TextContent) and block.text)
    structured = (
        json.dumps(result.structuredContent, ensure_ascii=False) if result.structuredContent is not None else None
    )
    if result.isError:
        raise MCPToolResultError(text or structured or "Tool reported an error without details.")
    # Machine-readable output is authoritative when a tool supplies both it
    # and a human-facing display summary. Preserve empty objects as well.
    if structured is not None:
        return structured
    if text:
        return text
    if result.content:
        return json.dumps(
            [block.model_dump(mode="json", exclude_none=True) for block in result.content],
            ensure_ascii=False,
        )
    return "Tool executed successfully with no content returned."
