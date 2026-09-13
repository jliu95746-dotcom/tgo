"""Discover MCP tools without saving or executing business operations."""
import asyncio
from contextlib import AsyncExitStack

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

from app.schemas.mcp_connection import mcp_connection_headers
from app.schemas.tool_probe import MCPDiscoverRequest, MCPDiscoverResponse, DiscoveredTool


def connection_error(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        return connection_error(exc.exceptions[0])
    if isinstance(exc, httpx.HTTPStatusError):
        return f"连接失败（HTTP {exc.response.status_code}），请检查地址及授权信息。"
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "连接超时，请检查服务是否运行及网络是否可达。"
    return "无法读取 MCP 工具，请检查地址、连接方式和授权信息。"


async def discover_mcp(request: MCPDiscoverRequest) -> MCPDiscoverResponse:
    try:
        headers = mcp_connection_headers({"headers": request.headers})
        async with asyncio.timeout(20), AsyncExitStack() as stack:
            if request.transport == "http":
                streams = await stack.enter_async_context(streamablehttp_client(request.endpoint, headers=headers))
            else:
                streams = await stack.enter_async_context(sse_client(request.endpoint, headers=headers))
            session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
            await session.initialize()
            result: list[DiscoveredTool] = []
            cursor = None
            for _ in range(10):
                page = await session.list_tools(cursor=cursor)
                result.extend(DiscoveredTool(name=tool.name, description=tool.description or "", input_schema=tool.inputSchema) for tool in page.tools)
                cursor = page.nextCursor
                if not cursor:
                    return MCPDiscoverResponse(success=True, tools=result)
            return MCPDiscoverResponse(success=False, error="服务返回的工具过多，请缩小服务范围。")
    except Exception as exc:
        return MCPDiscoverResponse(success=False, error=connection_error(exc))

