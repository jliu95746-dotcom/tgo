"""OpenAI-compatible Chat Completions API endpoint.

This module provides a fully OpenAI-compatible chat completions endpoint
that proxies requests to various LLM providers based on the provider_id.
"""

import uuid
from typing import Union

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.responses import ResponseDefinitions, build_error_responses
from app.core.logging import get_logger
from app.dependencies import get_db
from app.schemas.chat import (
    ChatCompletionRequest,
    ChatCompletionResponse,
)
from app.services.chat_service import ChatService
from app.services.quota_authorization import require_reply_quota

logger = get_logger(__name__)

router = APIRouter()


def get_chat_service(db: AsyncSession = Depends(get_db)) -> ChatService:
    """Get ChatService instance."""
    return ChatService(db)


_STREAMING_EXAMPLE = (
    'data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","crea'
    'ted":1234567890,"model":"gpt-4o","choices":[{"index":0,"delta":{"rol'
    'e":"assistant"},"finish_reason":null}]}\n'
    '\n'
    'data: {"type": "tool_call", "tool_call": {"id": "call_123", "type": '
    '"function", "function": {"name": "rag_search_abc123", "arguments": '
    '"{\\"query\\":\\"hello\\"}"}}}\n'
    '\n'
    'data: {"type": "tool_result", "tool_call_id": "call_123", "result": '
    '"<documents>...</documents>"}\n'
    '\n'
    'data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","crea'
    'ted":1234567890,"model":"gpt-4o","choices":[{"index":0,"delta":{"con'
    'tent":"Hello"},"finish_reason":null}]}\n'
    '\n'
    'data: [DONE]\n'
    '\n'
)


_completions_success_responses: ResponseDefinitions = {
    200: {
        "description": (
            'Successful response. Returns JSON when `stream=false` and '
            'Server-Sent Events when `stream=true`.'
        ),
        "content": {
            "application/json": {
                "schema": {
                    "$ref": "#/components/schemas/ChatCompletionResponse",
                },
                "example": {
                    "id": "chatcmpl-abc123",
                    "object": "chat.completion",
                    "created": 1234567890,
                    "model": "gpt-4",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "Hello! How can I help you today?",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 8,
                        "total_tokens": 18,
                    },
                },
            },
            "text/event-stream": {
                "schema": {
                    "type": "string",
                    "description": (
                        'Server-Sent Events (SSE) stream in OpenAI format '
                        'with TGO-AI tool extensions.'
                    ),
                },
                "examples": {
                    "streaming_response": {
                        "summary": (
                            'Streaming response example with tool calls'
                        ),
                        "value": _STREAMING_EXAMPLE,
                    },
                },
            },
        },
    }
}


@router.post(
    "/completions",
    dependencies=[Depends(require_reply_quota)],
    response_model=ChatCompletionResponse,
    responses={
        **_completions_success_responses,
        **build_error_responses(
            [400, 404, 500],
            {
                400: "请求参数错误或模型不支持该操作",
                404: "未找到指定的 LLM 供应商 (LLM Provider)",
                500: "后端 LLM 供应商调用失败或系统内部错误",
            },
        ),
    },
    summary="创建聊天补全 (Chat Completion)",
    description=(
        '\n'
        '创建一个聊天补全请求。该接口完全兼容 OpenAI 的 Chat Completions API '
        '格式，并扩展了对多供应商、动态工具加载和自动 RAG 搜索的支持。\n'
        '\n'
        '### 1. 核心请求参数 (Request Parameters)\n'
        '\n'
        '| 参数名 | 类型 | 是否必填 | 描述 |\n'
        '| :--- | :--- | :--- | :--- |\n'
        '| `provider_id` | UUID | 是 | 对应 `ai_llm_providers` 表中的供应商 '
        'ID。决定了使用哪组 API 密钥和厂商。 |\n'
        '| `model` | String | 是 | 模型标识符 (如 "gpt-4o", "claude-3-5-sonnet", '
        '"gemini-1.5-pro")。 |\n'
        '| `messages` | Array | 是 | 会话消息列表。支持 role 为 `system`, `user`, '
        '`assistant`, `tool`。 |\n'
        '| `stream` | Boolean | 否 | 是否开启流式输出。默认 `false`。 |\n'
        '| `temperature` | Float | 否 | 采样温度 (0-2)。 |\n'
        '| `max_tokens` | Integer | 否 | 最大生成 Token 数。 |\n'
        '\n'
        '### 2. TGO-AI 扩展参数 (TGO-AI Extensions)\n'
        '\n'
        '| 参数名 | 类型 | 描述 |\n'
        '| :--- | :--- | :--- |\n'
        '| `tool_ids` | Array[UUID] | 动态加载工具 ID 列表。后端会自动从数据库读取 MCP 工具定义并转换成 '
        'OpenAI function 格式发送给模型。 |\n'
        '| `collection_ids` | Array[String] | RAG 知识库 ID 列表。后端会为每个集合自动生成一个 '
        'RAG 搜索工具 (如 `rag_search_xxx`)。 |\n'
        '| `max_tool_rounds` | Integer | 自动工具调用的最大轮数 (Agentic Loop)。默认 '
        '`5`，最大 `20`。 |\n'
        '\n'
        '### 3. 返回值 (Return Value)\n'
        '\n'
        '#### 非流式模式 (stream=false)\n'
        '返回一个标准的 OpenAI 兼容 JSON 对象 (`ChatCompletionResponse`)。\n'
        '*   如果触发了工具调用且 `max_tool_rounds > 0`，后端会**自动执行工具**并在获取结果后再次请求 '
        'LLM，直到输出最终回复。\n'
        '*   `usage` 字段将包含整个 agentic loop 累积的 Token 消耗。\n'
        '\n'
        '#### 流式模式 (stream=true)\n'
        '返回 `text/event-stream`。除了标准的 OpenAI chunk 外，还会包含 TGO-AI 自定义的工具执行事件：\n'
        '\n'
        '| 事件类型 (SSE Data) | 描述 |\n'
        '| :--- | :--- |\n'
        '| `{"id":"...","object":"chat.completion.chunk",...}` | '
        '标准的文本增量或模型原始输出。 |\n'
        '| `{"type": "tool_call", "tool_call": {...}}` | **TGO-AI '
        '自定义事件**：通知前端后端正在自动执行某个工具调用。 |\n'
        '| `{"type": "tool_result", "tool_call_id": "...", "result": "..."}` '
        '| **TGO-AI 自定义事件**：工具执行完成，返回执行结果。 |\n'
        '| `data: [DONE]` | 流结束。 |\n'
        '\n'
        '### 4. 供应商支持情况 (Provider Support)\n'
        '*   **OpenAI / OpenAI Compatible**: 支持完整的工具调用和流式输出。\n'
        '*   **Anthropic (Claude)**: 支持工具调用。\n'
        '*   **Google (Gemini)**: 支持工具调用。\n'
        '\n'
        '### 5. 错误处理 (Error Handling)\n'
        '*   `404`: `provider_id` 不存在或不属于当前项目。\n'
        '*   `400`: `messages` 格式错误或 `max_tool_rounds` 超出范围。\n'
        '*   `500`: 远程供应商服务不可用或超时。\n'
    ),
)
async def create_chat_completion(
    request: ChatCompletionRequest,
    project_id: uuid.UUID = Query(
        ..., description="Project ID for authorization"
    ),
    chat_service: ChatService = Depends(get_chat_service),
) -> Union[ChatCompletionResponse, StreamingResponse]:
    (
        'Create a chat completion.\n'
        '\n'
        '    Supports both streaming and non-streaming responses based on '
        'the `stream` parameter.\n'
        '    Uses the specified LLM provider credentials to proxy the '
        'request to the actual LLM API.\n'
        '    '
    )
    logger.info(
        "Chat completion request",
        project_id=str(project_id),
        provider_id=str(request.provider_id),
        model=request.model,
        stream=request.stream,
        message_count=len(request.messages),
    )

    if request.stream:
        return StreamingResponse(
            chat_service.create_completion_stream(request, project_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    return await chat_service.create_completion(request, project_id)
