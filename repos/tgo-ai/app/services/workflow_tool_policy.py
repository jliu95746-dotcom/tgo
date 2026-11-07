"""Allow workflow nodes to execute only the supported read-only adapter."""

from pydantic import ValidationError

from app.models.tool import Tool, ToolSourceType, ToolType
from app.schemas.logistics_provider import LogisticsProviderConfig


def workflow_read_only_tool(tool: Tool) -> bool:
    """The logistics provider adapter performs a query, not a general HTTP action."""
    if (
        tool.tool_source_type != ToolSourceType.LOCAL
        or tool.tool_type != ToolType.FUNCTION
        or tool.transport_type != "http_webhook"
        or not tool.endpoint
        or not isinstance(tool.config, dict)
    ):
        return False
    try:
        config = LogisticsProviderConfig.model_validate(
            tool.config.get("logistics_provider")
        )
    except ValidationError:
        return False
    # Presets replace the caller's endpoint with a server-owned query endpoint.
    return config.provider_kind in {"kuaidi100", "kdniao"}
