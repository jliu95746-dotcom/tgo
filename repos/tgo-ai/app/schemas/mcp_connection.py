"""Validated connection settings shared by MCP execution paths."""

import re

from pydantic import BaseModel, Field, ValidationError, field_validator


class MCPConnectionConfig(BaseModel):
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, headers: dict[str, str]) -> dict[str, str]:
        seen: set[str] = set()
        for name, value in headers.items():
            if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
                raise ValueError("Invalid header name")
            if name.lower() in seen or any(ord(char) < 32 or ord(char) > 126 for char in value):
                raise ValueError("Invalid header value or duplicate header")
            seen.add(name.lower())
        return headers


def mcp_connection_headers(config: object) -> dict[str, str]:
    try:
        return MCPConnectionConfig.model_validate(config or {}).headers
    except ValidationError:
        # Pydantic errors contain input values; never expose credentials.
        raise ValueError("MCP 请求头配置无效") from None
