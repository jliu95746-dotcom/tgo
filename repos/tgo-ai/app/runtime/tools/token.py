"""MCP访问Token工具函数."""

from __future__ import annotations

from app.schemas.mcp_token import MCPAccessToken
from app.services.mcp_token_exchange import exchange_mcp_access_token


async def get_mcp_access_token(
    supabase_token: str,
    base_mcp_url: str,
) -> MCPAccessToken:
    """通过Supabase令牌交换MCP访问Token."""

    return await exchange_mcp_access_token(supabase_token, base_mcp_url)
