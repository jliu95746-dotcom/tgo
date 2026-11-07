"""Exchange a configured subject token without exposing provider failures."""

import asyncio

import aiohttp

from app.schemas.mcp_token import MCPAccessToken

TOKEN_EXCHANGE_TIMEOUT_SECONDS = 30.0


async def exchange_mcp_access_token(
    supabase_token: str, base_mcp_url: str,
) -> MCPAccessToken:
    form_data = {
        "client_id": "mcp_default",
        "subject_token": supabase_token,
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "resource": base_mcp_url.rstrip("/") + "/mcp",
        "subject_token_type": (
            "urn:ietf:params:oauth:token-type:access_token"
        ),
    }
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(
                total=TOKEN_EXCHANGE_TIMEOUT_SECONDS,
            ),
        ) as session:
            async with session.post(
                base_mcp_url.rstrip("/") + "/oauth/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data=form_data,
            ) as response:
                if response.status != 200:
                    raise RuntimeError(
                        "Failed to exchange MCP token "
                        f"(HTTP {response.status})",
                    )
                try:
                    return MCPAccessToken.model_validate(await response.json())
                except (TypeError, ValueError):
                    raise RuntimeError(
                        "Invalid MCP token exchange response",
                    ) from None
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise RuntimeError("Unable to exchange MCP access token") from None
