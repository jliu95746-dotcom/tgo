"""Read-only discovery contract for a user-configured MCP server."""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, JsonValue, field_validator


class MCPDiscoverRequest(BaseModel):
    endpoint: str = Field(max_length=2048)
    transport: Literal["http", "sse"] = "http"
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Use an HTTP(S) service address without embedded credentials")
        return value


class DiscoveredTool(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, JsonValue] = Field(default_factory=dict)


class MCPDiscoverResponse(BaseModel):
    success: bool
    tools: list[DiscoveredTool] = Field(default_factory=list)
    error: str | None = None


class ToolTestRequest(BaseModel):
    input_data: dict[str, JsonValue] = Field(default_factory=dict)


class ToolTestResponse(BaseModel):
    success: bool
    output_data: JsonValue = None
    error: str | None = None

