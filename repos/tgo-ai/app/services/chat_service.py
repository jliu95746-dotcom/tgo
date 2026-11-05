"""Chat completion service for OpenAI-compatible API.

This service proxies chat completion requests to various LLM providers
based on the provider_kind from the LLMProvider table.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from google import genai
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.exceptions import TGOAIServiceException
from app.models.llm_provider import LLMProvider
from app.models.tool import Tool
from app.schemas.chat import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    ChoiceMessage,
    DeltaMessage,
    FunctionCall,
    FunctionDefinition,
    StreamChoice,
    ToolCall,
    ToolDefinition,
    Usage,
)
from app.services import chat_anthropic, chat_google
from app.services.chat_provider_protocol import enforce_tool_policy
from app.services.llm_provider_service import LLMProviderService
from app.services.rag_service import rag_service_client
from app.services.tool_executor import ToolExecutor
from app.services.chat_accounting import (
    account_completion, account_stream, close_provider_stream,
)
from app.services.commercial_ai_limits import constrain_chat
from app.services.chat_tool_calls import (
    INVALID_TOOL_CALL_MESSAGE, InvalidToolCallError, PreparedToolCall,
    ToolCallStream,
    validate_tool_calls,
)

logger = get_logger(__name__)


# =============================================================================
# Custom Exceptions
# =============================================================================


class ProviderNotFoundError(TGOAIServiceException):
    """Raised when the specified LLM provider is not found."""

    def __init__(self, provider_id: uuid.UUID):
        super().__init__(
            code="PROVIDER_NOT_FOUND",
            message=f"LLM provider with ID {provider_id} not found",
            details={"provider_id": str(provider_id)},
        )


class ProviderNotActiveError(TGOAIServiceException):
    """Raised when the specified LLM provider is not active."""

    def __init__(self, provider_id: uuid.UUID):
        super().__init__(
            code="PROVIDER_NOT_ACTIVE",
            message=f"LLM provider with ID {provider_id} is not active",
            details={"provider_id": str(provider_id)},
        )


class UnsupportedProviderError(TGOAIServiceException):
    """Raised when the provider kind is not supported."""

    def __init__(self, provider_kind: str):
        super().__init__(
            code="UNSUPPORTED_PROVIDER",
            message=f"Provider kind '{provider_kind}' is not supported",
            details={"provider_kind": provider_kind},
        )


class ChatCompletionError(TGOAIServiceException):
    """Raised when chat completion fails."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            code="CHAT_COMPLETION_ERROR",
            message=message,
            details=details or {},
        )


# =============================================================================
# Chat Service
# =============================================================================


class ChatService:
    """Service for handling chat completions via various LLM providers."""

    # Provider kind constants
    OPENAI_PROVIDERS = {"openai", "openai_compatible"}
    ANTHROPIC_PROVIDER = "anthropic"
    GOOGLE_PROVIDER = "google"

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.provider_service = LLMProviderService(db)
        self._logger = logger

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def create_completion(
        self,
        request: ChatCompletionRequest,
        project_id: uuid.UUID,
    ) -> ChatCompletionResponse:
        """Create a non-streaming chat completion with optional agentic loop."""
        request = constrain_chat(request)
        provider = await self._get_provider(request.provider_id, project_id)
        provider_kind = (provider.provider_kind or "").lower()

        # Merge tools from all sources
        merged_tools = await self._merge_tools(request, project_id)
        request.tools = merged_tools

        max_rounds = request.max_tool_rounds if request.max_tool_rounds is not None else 5

        self._logger.info(
            "Creating chat completion",
            provider_id=str(request.provider_id),
            provider_kind=provider_kind,
            model=request.model,
            tools_count=len(merged_tools) if merged_tools else 0,
            auto_execute_tools=request.auto_execute_tools,
            max_tool_rounds=max_rounds,
        )

        # When auto_execute_tools=False, behave like standard OpenAI API:
        # call LLM once and return the raw response (including any tool_calls).
        if not request.auto_execute_tools:
            if provider_kind in self.OPENAI_PROVIDERS:
                return await self._openai_completion(request, provider)
            elif provider_kind == self.ANTHROPIC_PROVIDER:
                return await self._anthropic_completion(request, provider)
            elif provider_kind == self.GOOGLE_PROVIDER:
                return await self._google_completion(request, provider)
            else:
                raise UnsupportedProviderError(provider_kind)

        # auto_execute_tools=True: run agentic loop with internal tool execution
        # Create tool executor and register tools
        executor = ToolExecutor(self.db, project_id)
        # Set context for plugin tools
        executor.set_context(
            visitor_id=request.user, # request.user contains visitor_id
            agent_id=str(request.agent_id) if hasattr(request, "agent_id") else None,
            knowledge_channel=(
                request.knowledge_channel.value if request.knowledge_channel else None
            ),
        )
        await executor.register_tools(request.tool_ids, request.collection_ids)

        total_usage = Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
        usage_known = True
        
        for round_idx in range(max_rounds):
            self._logger.debug(f"Starting tool round {round_idx + 1}/{max_rounds}")
            
            if provider_kind in self.OPENAI_PROVIDERS:
                response = await self._openai_completion(request, provider)
            elif provider_kind == self.ANTHROPIC_PROVIDER:
                response = await self._anthropic_completion(request, provider)
            elif provider_kind == self.GOOGLE_PROVIDER:
                response = await self._google_completion(request, provider)
            else:
                raise UnsupportedProviderError(provider_kind)

            # Update usage
            if response.usage:
                total_usage.prompt_tokens += response.usage.prompt_tokens
                total_usage.completion_tokens += response.usage.completion_tokens
                total_usage.total_tokens += response.usage.total_tokens
            else:
                usage_known = False

            # Check if there are tool calls in the first choice
            choice = next(
                (item for item in response.choices if item.index == 0), None,
            )
            if choice is None or not choice.message.tool_calls:
                response.usage = total_usage if usage_known else None
                return response

            try:
                prepared = validate_tool_calls(
                    choice.message.tool_calls, choice.finish_reason,
                )
                enforce_tool_policy(request, prepared)
            except InvalidToolCallError:
                raise ChatCompletionError(INVALID_TOOL_CALL_MESSAGE) from None
            assistant_msg_content = choice.message.content
            tool_calls = [item.protocol_call() for item in prepared]
            
            # Add assistant message with tool calls to history
            request.messages.append(ChatMessage(
                role="assistant",
                content=assistant_msg_content,
                tool_calls=tool_calls,
                provider_state=choice.message.provider_state,
            ))

            # Execute tool calls in parallel
            execution_tasks = []
            for item in prepared:
                execution_tasks.append(executor.execute(
                    item.name, item.arguments,
                ))
            
            results = await asyncio.gather(*execution_tasks)

            # Add tool result messages to history
            for item, result in zip(prepared, results):
                request.messages.append(ChatMessage(
                    role="tool",
                    name=item.name,
                    content=result,
                    tool_call_id=item.identifier
                ))
            
            # Continue to next round

        # If max rounds reached, do one final call without tools
        self._logger.warning(f"Reached max tool rounds ({max_rounds}), finishing without tools")
        request.tools = None
        request.tool_choice = None
        if provider_kind in self.OPENAI_PROVIDERS:
            response = await self._openai_completion(request, provider)
        elif provider_kind == self.ANTHROPIC_PROVIDER:
            response = await self._anthropic_completion(request, provider)
        elif provider_kind == self.GOOGLE_PROVIDER:
            response = await self._google_completion(request, provider)
        else:
            raise UnsupportedProviderError(provider_kind)
            
        if response.usage:
            total_usage.prompt_tokens += response.usage.prompt_tokens
            total_usage.completion_tokens += response.usage.completion_tokens
            total_usage.total_tokens += response.usage.total_tokens
        else:
            usage_known = False
        if any(choice.message.tool_calls for choice in response.choices):
            raise ChatCompletionError(INVALID_TOOL_CALL_MESSAGE)
        response.usage = total_usage if usage_known else None

        return response

    async def create_completion_stream(
        self,
        request: ChatCompletionRequest,
        project_id: uuid.UUID,
    ) -> AsyncIterator[str]:
        """Create a streaming chat completion with optional agentic loop."""
        request = constrain_chat(request)
        provider = await self._get_provider(request.provider_id, project_id)
        provider_kind = (provider.provider_kind or "").lower()

        # Merge tools from all sources
        merged_tools = await self._merge_tools(request, project_id)
        request.tools = merged_tools

        max_rounds = request.max_tool_rounds if request.max_tool_rounds is not None else 5

        self._logger.info(
            "Creating streaming chat completion",
            provider_id=str(request.provider_id),
            provider_kind=provider_kind,
            model=request.model,
            tools_count=len(merged_tools) if merged_tools else 0,
            auto_execute_tools=request.auto_execute_tools,
            max_tool_rounds=max_rounds,
        )

        # When auto_execute_tools=False, behave like standard OpenAI API:
        # stream LLM response as-is (including tool_call chunks).
        if not request.auto_execute_tools:
            if provider_kind in self.OPENAI_PROVIDERS:
                stream_gen = self._openai_stream(request, provider)
            elif provider_kind == self.ANTHROPIC_PROVIDER:
                stream_gen = self._anthropic_stream(request, provider)
            elif provider_kind == self.GOOGLE_PROVIDER:
                stream_gen = self._google_stream(request, provider)
            else:
                raise UnsupportedProviderError(provider_kind)

            try:
                async for raw_chunk in stream_gen:
                    yield raw_chunk
            finally:
                await close_provider_stream(stream_gen)
            return

        # auto_execute_tools=True: run agentic loop with internal tool execution
        # Create tool executor
        executor = ToolExecutor(self.db, project_id)
        executor.set_context(
            visitor_id=request.user,
            knowledge_channel=(
                request.knowledge_channel.value if request.knowledge_channel else None
            ),
        )
        await executor.register_tools(request.tool_ids, request.collection_ids)

        prepared: tuple[PreparedToolCall, ...] = ()
        round_idx = -1
        for round_idx in range(max_rounds):
            self._logger.debug(f"Starting streaming tool round {round_idx + 1}/{max_rounds}")
            
            tool_stream = ToolCallStream()
            
            if provider_kind in self.OPENAI_PROVIDERS:
                stream_gen = self._openai_stream(request, provider)
            elif provider_kind == self.ANTHROPIC_PROVIDER:
                stream_gen = self._anthropic_stream(request, provider)
            elif provider_kind == self.GOOGLE_PROVIDER:
                stream_gen = self._google_stream(request, provider)
            else:
                raise UnsupportedProviderError(provider_kind)

            try:
                async for raw_chunk in stream_gen:
                    if tool_stream.observe(raw_chunk):
                        yield raw_chunk
                prepared = tool_stream.finish()
                enforce_tool_policy(request, prepared)
            except Exception as exc:
                self._logger.error(
                    "Chat tool stream rejected before execution",
                    error_type=type(exc).__name__,
                )
                yield "data: " + json.dumps({"error": {
                    "message": INVALID_TOOL_CALL_MESSAGE,
                    "type": "api_error",
                }}, ensure_ascii=False) + "\n\n"
                yield "data: [DONE]\n\n"
                return
            finally:
                await close_provider_stream(stream_gen)

            if not prepared:
                yield "data: [DONE]\n\n"
                return

            final_tool_calls = [item.protocol_call() for item in prepared]

            request.messages.append(ChatMessage(
                role="assistant",
                content=tool_stream.content,
                tool_calls=final_tool_calls,
                provider_state=tool_stream.provider_state,
            ))

            for tc in final_tool_calls:
                yield f"data: {json.dumps({'type': 'tool_call', 'tool_call': tc.model_dump()}, ensure_ascii=False)}\n\n"

            execution_tasks = [
                executor.execute(item.name, item.arguments)
                for item in prepared
            ]
            results = await asyncio.gather(*execution_tasks)
            
            for item, result in zip(prepared, results):
                yield "data: " + json.dumps({
                    "type": "tool_result",
                    "tool_call_id": item.identifier, "result": result,
                }, ensure_ascii=False) + "\n\n"
                request.messages.append(ChatMessage(
                    role="tool",
                    name=item.name,
                    content=result,
                    tool_call_id=item.identifier
                ))
            
            if round_idx == max_rounds - 1:
                self._logger.warning(f"Reached max tool rounds ({max_rounds}), finishing")
                break

        # Final call without tools if we broke out of the loop with tool calls
        if prepared and round_idx == max_rounds - 1:
            request.tools = None
            request.tool_choice = None
            if provider_kind in self.OPENAI_PROVIDERS:
                stream_gen = self._openai_stream(request, provider)
            elif provider_kind == self.ANTHROPIC_PROVIDER:
                stream_gen = self._anthropic_stream(request, provider)
            elif provider_kind == self.GOOGLE_PROVIDER:
                stream_gen = self._google_stream(request, provider)
            else:
                raise UnsupportedProviderError(provider_kind)
            
            final_stream = ToolCallStream()
            try:
                async for raw_chunk in stream_gen:
                    if final_stream.observe(raw_chunk):
                        yield raw_chunk
                if final_stream.finish():
                    raise InvalidToolCallError()
            except Exception as exc:
                self._logger.error(
                    "Final chat stream rejected",
                    error_type=type(exc).__name__,
                )
                yield "data: " + json.dumps({"error": {
                    "message": INVALID_TOOL_CALL_MESSAGE, "type": "api_error",
                }}, ensure_ascii=False) + "\n\n"
            finally:
                await close_provider_stream(stream_gen)
            yield "data: [DONE]\n\n"
        else:
            yield "data: [DONE]\n\n"

    # -------------------------------------------------------------------------
    # Tool Merging Logic
    # -------------------------------------------------------------------------

    async def _merge_tools(
        self,
        request: ChatCompletionRequest,
        project_id: uuid.UUID,
    ) -> Optional[List[ToolDefinition]]:
        """Merge tools from request.tools, tool_ids, and collection_ids."""
        import asyncio
        
        db_tools_task = None
        if request.tool_ids:
            db_tools_task = asyncio.create_task(self._load_tools_from_db(request.tool_ids, project_id))

        rag_tools_task = None
        if request.collection_ids:
            rag_tools_task = asyncio.create_task(self._create_rag_tool_definitions(request.collection_ids, project_id))

        db_tools = await db_tools_task if db_tools_task else []
        rag_tools = await rag_tools_task if rag_tools_task else []

        # Merge all tools
        merged_tools: List[ToolDefinition] = []
        if request.tools:
            merged_tools.extend(request.tools)
        
        merged_tools.extend(db_tools)
        merged_tools.extend(rag_tools)

        return merged_tools if merged_tools else None

    async def _load_tools_from_db(
        self,
        tool_ids: List[uuid.UUID],
        project_id: uuid.UUID,
    ) -> List[ToolDefinition]:
        """Load MCP tools from database and convert to ToolDefinition."""
        if not tool_ids:
            return []

        stmt = select(Tool).where(
            and_(
                Tool.id.in_(tool_ids),
                Tool.project_id == project_id,
                Tool.deleted_at.is_(None),
            )
        )
        result = await self.db.execute(stmt)
        tools = result.scalars().all()

        tool_defs = []
        for tool in tools:
            # We expect tool.config to contain 'inputSchema' for MCP tools
            parameters = tool.config.get("inputSchema", {}) if tool.config else {}
            
            tool_defs.append(
                ToolDefinition(
                    type="function",
                    function=FunctionDefinition(
                        name=tool.name,
                        description=tool.description,
                        parameters=parameters,
                    ),
                )
            )
        
        return tool_defs

    async def _create_rag_tool_definitions(
        self,
        collection_ids: List[str],
        project_id: uuid.UUID,
    ) -> List[ToolDefinition]:
        """Fetch collection info from RAG service and create RAG tool definitions."""
        if not collection_ids:
            return []

        tool_defs = []
        try:
            # We use the batch client to fetch info for all collections
            batch_resp = await rag_service_client.get_collections_batch(
                collection_ids, str(project_id)
            )
            
            # Map of found collections
            found_collections = {str(c.id): c for c in batch_resp.collections}

            for cid in collection_ids:
                if cid in found_collections:
                    col = found_collections[cid]
                    display_name = col.display_name
                    description = col.description or f"Search in {display_name}"
                else:
                    display_name = f"collection_{cid[:8]}"
                    description = f"Search in collection {cid}"

                tool_name = f"rag_search_{cid.replace('-', '')[:8]}"
                tool_defs.append(
                    ToolDefinition(
                        type="function",
                        function=FunctionDefinition(
                            name=tool_name,
                            description=f"{description} (Semantically similar search)",
                            parameters={
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type": "string",
                                        "description": "Natural language search query",
                                    }
                                },
                                "required": ["query"],
                            },
                        ),
                    )
                )
        except Exception as exc:
            self._logger.warning(
                "Failed to create RAG tool definitions",
                error_type=type(exc).__name__,
            )
            # Fallback to generic definitions if RAG service call fails
            for cid in collection_ids:
                tool_name = f"rag_search_{cid.replace('-', '')[:8]}"
                tool_defs.append(
                    ToolDefinition(
                        type="function",
                        function=FunctionDefinition(
                            name=tool_name,
                            description=f"Search in collection {cid} (Semantically similar search)",
                            parameters={
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type": "string",
                                        "description": "Natural language search query",
                                    }
                                },
                                "required": ["query"],
                            },
                        ),
                    )
                )

        return tool_defs

    # -------------------------------------------------------------------------
    # Provider Validation
    # -------------------------------------------------------------------------

    async def _get_provider(
        self,
        provider_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> LLMProvider:
        """Get and validate the LLM provider."""
        from app.services.platform_models import provider_for_request
        platform_provider = provider_for_request(provider_id, project_id)
        if platform_provider is not None:
            return platform_provider
        provider = await self.provider_service.get_provider_by_id(provider_id)

        if not provider or provider.project_id != project_id:
            raise ProviderNotFoundError(provider_id)

        if not provider.is_active:
            raise ProviderNotActiveError(provider_id)

        return provider

    # -------------------------------------------------------------------------
    # OpenAI / OpenAI-Compatible Implementation
    # -------------------------------------------------------------------------

    def _create_openai_client(self, provider: LLMProvider) -> AsyncOpenAI:
        """Create an OpenAI client from provider credentials."""
        return AsyncOpenAI(
            api_key=provider.api_key,
            base_url=provider.api_base_url,
            organization=provider.organization,
            timeout=provider.timeout or 60.0,
        )

    @account_completion
    async def _openai_completion(
        self,
        request: ChatCompletionRequest,
        provider: LLMProvider,
    ) -> ChatCompletionResponse:
        """Handle OpenAI/OpenAI-compatible chat completion."""
        try:
            client = self._create_openai_client(provider)
            params = self._build_openai_params(request)
            response = await client.chat.completions.create(**params)

            return ChatCompletionResponse(
                id=response.id,
                created=response.created,
                model=response.model,
                choices=[
                    Choice(
                        index=choice.index,
                        message=ChoiceMessage(
                            role="assistant",
                            content=choice.message.content,
                            tool_calls=self._convert_openai_tool_calls(choice.message.tool_calls),
                        ),
                        finish_reason=choice.finish_reason,
                    )
                    for choice in response.choices
                ],
                usage=Usage(
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                    total_tokens=response.usage.total_tokens,
                ) if response.usage else None,
                system_fingerprint=response.system_fingerprint,
            )
        except Exception as exc:
            self._logger.error(
                "OpenAI completion failed",
                error_type=type(exc).__name__,
            )
            raise ChatCompletionError(
                "OpenAI completion failed",
                details={"provider_kind": "openai", "model": request.model},
            ) from exc

    @account_stream
    async def _openai_stream(
        self,
        request: ChatCompletionRequest,
        provider: LLMProvider,
    ) -> AsyncIterator[str]:
        """Handle OpenAI/OpenAI-compatible streaming chat completion."""
        try:
            client = self._create_openai_client(provider)
            params = self._build_openai_params(request)
            params["stream"] = True

            stream = await client.chat.completions.create(**params)

            async for chunk in stream:
                chunk_data = ChatCompletionChunk(
                    id=chunk.id,
                    created=chunk.created,
                    model=chunk.model,
                    choices=[
                        StreamChoice(
                            index=choice.index,
                            delta=DeltaMessage(
                                role=getattr(choice.delta, "role", None),
                                content=getattr(choice.delta, "content", None),
                                tool_calls=self._convert_openai_tool_calls(getattr(choice.delta, "tool_calls", None)),
                            ),
                            finish_reason=choice.finish_reason,
                        )
                        for choice in chunk.choices
                    ],
                    system_fingerprint=chunk.system_fingerprint,
                )
                yield f"data: {chunk_data.model_dump_json()}\n\n"

            yield "data: [DONE]\n\n"
        except Exception as exc:
            self._logger.error(
                "OpenAI streaming failed",
                error_type=type(exc).__name__,
            )
            yield f"data: {json.dumps({'error': {'message': 'OpenAI streaming failed', 'type': 'api_error'}})}\n\n"

    def _convert_openai_tool_calls(self, openai_tool_calls: Optional[List[Any]]) -> Optional[List[ToolCall]]:
        """Convert OpenAI SDK tool calls to internal ToolCall schema."""
        if not openai_tool_calls:
            return None
        
        result = []
        for tc in openai_tool_calls:
            if isinstance(tc, dict):
                tc_index = tc.get("index")
                tc_id = tc.get("id")
                tc_type = tc.get("type", "function")
                func_data = tc.get("function")
                f_name = func_data.get("name") if func_data else None
                f_args = func_data.get("arguments") if func_data else None
            else:
                tc_index = getattr(tc, "index", None)
                tc_id = getattr(tc, "id", None)
                tc_type = getattr(tc, "type", "function")
                func_obj = getattr(tc, "function", None)
                f_name = getattr(func_obj, "name", None) if func_obj else None
                f_args = getattr(func_obj, "arguments", None) if func_obj else None
            
            result.append(ToolCall(
                index=tc_index,
                id=tc_id,
                type=tc_type,
                function=FunctionCall(
                    name=f_name,
                    arguments=f_args
                )
            ))
        return result if result else None

    def _build_openai_params(self, request: ChatCompletionRequest) -> Dict[str, Any]:
        """Build OpenAI API parameters from request."""
        params: Dict[str, Any] = {
            "model": request.model,
            "messages": [self._format_openai_message(msg) for msg in request.messages],
        }

        # Map optional parameters
        optional_params = {
            "temperature": request.temperature,
            "top_p": request.top_p,
            "n": request.n,
            "max_tokens": request.max_tokens,
            "stop": request.stop,
            "presence_penalty": request.presence_penalty,
            "frequency_penalty": request.frequency_penalty,
            "logit_bias": request.logit_bias,
            "user": request.user,
            "seed": request.seed,
        }

        for key, value in optional_params.items():
            if value is not None:
                params[key] = value

        # Handle complex parameters
        if request.tools:
            params["tools"] = [tool.model_dump() for tool in request.tools]
        if request.tool_choice is not None:
            params["tool_choice"] = request.tool_choice
        if request.response_format:
            params["response_format"] = request.response_format.model_dump()

        return params

    @staticmethod
    def _format_openai_message(msg: ChatMessage) -> Dict[str, Any]:
        """Format a single message for OpenAI API."""
        result: Dict[str, Any] = {"role": msg.role, "content": msg.content}
        if msg.name:
            result["name"] = msg.name
        if msg.tool_calls:
            result["tool_calls"] = [tc.model_dump() for tc in msg.tool_calls]
        if msg.tool_call_id:
            result["tool_call_id"] = msg.tool_call_id
        return result

    # -------------------------------------------------------------------------
    # Anthropic Implementation
    # -------------------------------------------------------------------------

    def _create_anthropic_client(
        self, provider: LLMProvider
    ) -> AsyncAnthropic:
        if not provider.api_key or not provider.api_key.strip():
            raise ChatCompletionError("供应商未配置 API 密钥")
        return AsyncAnthropic(
            api_key=provider.api_key,
            base_url=provider.api_base_url,
            timeout=provider.timeout or 60.0,
        )

    def _create_google_client(self, provider: LLMProvider) -> genai.Client:
        if not provider.api_key or not provider.api_key.strip():
            raise ChatCompletionError("供应商未配置 API 密钥")
        return genai.Client(
            api_key=provider.api_key,
            vertexai=False,
            http_options=genai.types.HttpOptions(
                base_url=provider.api_base_url,
                timeout=int((provider.timeout or 60.0) * 1000),
            ),
        )

    @account_completion
    async def _anthropic_completion(
        self,
        request: ChatCompletionRequest,
        provider: LLMProvider,
    ) -> ChatCompletionResponse:
        try:
            return await chat_anthropic.complete(
                request, self._create_anthropic_client(provider)
            )
        except Exception as exc:
            self._logger.error(
                "Anthropic completion failed", error_type=type(exc).__name__
            )
            raise ChatCompletionError("Anthropic completion failed") from None

    @account_stream
    async def _anthropic_stream(
        self,
        request: ChatCompletionRequest,
        provider: LLMProvider,
    ) -> AsyncIterator[str]:
        chunks: AsyncIterator[str] | None = None
        try:
            chunks = chat_anthropic.stream(
                request,
                self._create_anthropic_client(provider),
            )
            async for chunk in chunks:
                yield chunk
        except Exception as exc:
            self._logger.error(
                "Anthropic streaming failed", error_type=type(exc).__name__
            )
            yield (
                'data: {"error":{"message":"Anthropic streaming failed",'
                '"type":"api_error"}}\n\n'
            )
        finally:
            if chunks is not None:
                await close_provider_stream(chunks)

    @account_completion
    async def _google_completion(
        self,
        request: ChatCompletionRequest,
        provider: LLMProvider,
    ) -> ChatCompletionResponse:
        try:
            return await chat_google.complete(
                request, self._create_google_client(provider)
            )
        except Exception as exc:
            self._logger.error(
                "Google completion failed", error_type=type(exc).__name__
            )
            raise ChatCompletionError("Google completion failed") from None

    @account_stream
    async def _google_stream(
        self,
        request: ChatCompletionRequest,
        provider: LLMProvider,
    ) -> AsyncIterator[str]:
        chunks: AsyncIterator[str] | None = None
        try:
            chunks = chat_google.stream(
                request,
                self._create_google_client(provider),
            )
            async for chunk in chunks:
                yield chunk
        except Exception as exc:
            self._logger.error(
                "Google streaming failed", error_type=type(exc).__name__
            )
            yield (
                'data: {"error":{"message":"Google streaming failed",'
                '"type":"api_error"}}\n\n'
            )
        finally:
            if chunks is not None:
                await close_provider_stream(chunks)
