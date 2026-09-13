"""Keep bindings with different transports or credentials in separate sessions."""

from app.models.internal import AgentTool
from app.schemas.mcp_connection import mcp_connection_headers


def group_mcp_bindings(
    tools_by_endpoint: dict[str, list[AgentTool]],
) -> list[tuple[str, list[AgentTool]]]:
    groups: dict[tuple[str, str, str, tuple[tuple[str, str], ...]], list[AgentTool]] = {}
    for endpoint, tools in tools_by_endpoint.items():
        for tool in tools:
            source = tool.tool_source_type or "LOCAL"
            headers = mcp_connection_headers(tool.base_config) if source != "STORE" else {}
            key = (endpoint, tool.transport_type or "http", source, tuple(sorted(headers.items())))
            groups.setdefault(key, []).append(tool)
    return [(key[0], tools) for key, tools in groups.items()]
