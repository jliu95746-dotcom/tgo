"""工具运行时的辅助方法."""

from __future__ import annotations

import json
import logging
import time
from builtins import ExceptionGroup
from datetime import UTC, datetime
from functools import wraps
from typing import Any, Dict, List, Optional
from uuid import UUID

import aiohttp
from agno.tools import Function
from mcp import ClientSession, McpError
from mcp.client.streamable_http import streamablehttp_client

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.usage import CollectionUsageRecord, ToolUsageRecord
from app.schemas.knowledge import KnowledgeChannel

logger = logging.getLogger(__name__)

NO_RELEVANT_KNOWLEDGE_RESPONSE = (
    '<documents status="no_relevant_knowledge">'
    "<instruction>No sufficiently relevant approved knowledge was found. "
    "Do not answer the factual question from guesses or general knowledge. "
    "Tell the user it cannot be confirmed from the knowledge base and offer "
    "human support.</instruction>"
    "</documents>"
)

RAG_SEARCH_LIMIT_RESPONSE = (
    '<documents status="search_limit_reached">'
    "<instruction>The knowledge collection has already been searched for this "
    "user turn. Do not call the RAG tool again. Answer only from the documents "
    "already returned; if they do not contain an explicit match, say that no "
    "matching item can be confirmed.</instruction>"
    "</documents>"
)

RAG_GROUNDING_INSTRUCTION = (
    "<instruction>This is the complete governed result for this turn. Call no "
    "RAG tool again. Use only explicit facts in these documents. Never claim "
    "that an item matches a requested price, policy, date, specification, or "
    "other constraint unless the matching value is explicitly present. If no "
    "document satisfies the constraint, clearly state that no confirmed match "
    "was found.</instruction>"
)

RAG_RESULT_LIMIT = 4
RAG_DOCUMENT_CHARACTER_LIMIT = 1600


async def _record_collection_usage(
    *,
    project_id: str,
    agent_id: str | None,
    collection_id: str,
    session_id: str | None,
    user_id: str | None,
    query: str,
    filters: Optional[Dict[str, Any]],
    documents: list[dict[str, Any]],
    duration_ms: int,
    status: str,
    error_message: str | None = None,
) -> None:
    """Persist RAG analytics without affecting the customer response."""
    if not agent_id:
        return
    try:
        project_uuid = UUID(project_id)
        agent_uuid = UUID(agent_id)
    except ValueError:
        return

    scores = [
        float(document["score"])
        for document in documents
        if isinstance(document.get("score"), (int, float))
    ]
    now = datetime.now(UTC)
    record = CollectionUsageRecord(
        project_id=project_uuid,
        agent_id=agent_uuid,
        collection_id=collection_id,
        session_id=session_id,
        user_id=user_id,
        query_text=query,
        query_type="semantic_search",
        query_parameters={"filters": filters, "limit": RAG_RESULT_LIMIT},
        documents_retrieved=len(documents),
        retrieved_documents={
            "documents": [
                {
                    "document_id": document.get("document_id"),
                    "score": document.get("score"),
                }
                for document in documents
            ]
        },
        max_relevance_score=max(scores) if scores else None,
        avg_relevance_score=(sum(scores) / len(scores)) if scores else None,
        query_duration_ms=duration_ms,
        query_status=status,
        error_message=error_message,
        started_at=now,
        completed_at=now,
    )
    try:
        async with AsyncSessionLocal() as analytics_session:
            analytics_session.add(record)
            await analytics_session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to persist RAG usage analytics: %s", exc)


def _sanitize_tool_value(value: Any) -> Any:
    """Keep analytics useful without persisting common credential fields."""
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).lower()
            if any(
                marker in normalized_key
                for marker in (
                    "password",
                    "secret",
                    "token",
                    "api_key",
                    "authorization",
                )
            ):
                sanitized[str(key)] = "***"
            else:
                sanitized[str(key)] = _sanitize_tool_value(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_tool_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


async def _record_tool_usage(
    *,
    project_id: str | None,
    agent_id: str | None,
    tool_name: str,
    session_id: str | None,
    user_id: str | None,
    arguments: dict[str, Any],
    result: Any,
    duration_ms: int,
    status: str,
    error_message: str | None = None,
) -> None:
    """Persist MCP tool analytics without blocking tool execution."""
    if not project_id or not agent_id:
        return
    try:
        project_uuid = UUID(project_id)
        agent_uuid = UUID(agent_id)
    except ValueError:
        return
    now = datetime.now(UTC)
    safe_result = _sanitize_tool_value(result)
    if isinstance(safe_result, str) and len(safe_result) > 2000:
        safe_result = safe_result[:2000] + "…"
    record = ToolUsageRecord(
        project_id=project_uuid,
        agent_id=agent_uuid,
        tool_name=f"mcp:{tool_name}",
        session_id=session_id,
        user_id=user_id,
        input_parameters=_sanitize_tool_value(arguments),
        execution_result={"value": safe_result},
        execution_status=status,
        error_message=error_message,
        execution_duration_ms=duration_ms,
        started_at=now,
        completed_at=now,
    )
    try:
        async with AsyncSessionLocal() as analytics_session:
            analytics_session.add(record)
            await analytics_session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to persist tool usage analytics: %s", exc)


async def create_rag_tool(
    rag_url: str,
    collection_id: str,
    project_id: Optional[str],
    *,
    knowledge_channel: KnowledgeChannel | str,
    filters: Optional[Dict[str, Any]] = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
) -> Function:
    """根据集合信息生成RAG查询工具."""

    if not project_id:
        raise ValueError("project_id is required to create RAG tools")
    try:
        channel = KnowledgeChannel(knowledge_channel)
    except ValueError as exc:
        raise ValueError(
            "a supported knowledge_channel is required to create RAG tools"
        ) from exc

    url = rag_url.rstrip("/")
    collection_endpoint = f"{url}/v1/collections/{collection_id}"
    params = {"project_id": str(project_id)}

    async with aiohttp.ClientSession() as session:
        async with session.get(collection_endpoint, params=params) as response:
            response.raise_for_status()
            collection_data = await response.json()
    display_name = collection_data.get("display_name") or f"collection_{collection_id}"
    description = collection_data.get("description")
    tool_description = (
        f"Search documents within the '{display_name}' collection for results"
        " semantically similar to the query. If no relevant documents are"
        " returned, do not guess or answer from general knowledge."
    )
    if description:
        tool_description = f"{tool_description} Collection description: {description}"

    # Build provider-safe tool name: letters, digits, _, ., -
    # We no longer use display_name in the tool name to avoid special character issues.
    short_id = (collection_id.replace("-", "")[:8]) if collection_id else "unknown"
    tool_name = f"rag_search_{short_id}".lower()
    search_executed = False

    async def search_collection(query: str) -> str:
        nonlocal search_executed
        if search_executed:
            return RAG_SEARCH_LIMIT_RESPONSE
        search_executed = True

        search_endpoint = (
            f"{url}/v1/collections/{collection_id}" "/documents/search/automatic-answer"
        )
        payload = {
            "query": query,
            "limit": RAG_RESULT_LIMIT,
            "min_score": settings.rag_min_similarity_score,
            "filters": filters,
            "channel": channel.value,
        }
        started_at = time.perf_counter()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    search_endpoint,
                    params=params,
                    json=payload,
                ) as search_response:
                    search_response.raise_for_status()
                    data = await search_response.json()
        except Exception as exc:  # noqa: BLE001 - 需要返回错误信息
            await _record_collection_usage(
                project_id=str(project_id),
                agent_id=agent_id,
                collection_id=collection_id,
                session_id=session_id,
                user_id=user_id,
                query=query,
                filters=filters,
                documents=[],
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                status="error",
                error_message=str(exc),
            )
            return f"<error>{exc}</error>"

        documents = data.get("results", [])
        governed_documents = [
            document
            for document in documents[:RAG_RESULT_LIMIT]
            if isinstance(document, dict)
        ]
        await _record_collection_usage(
            project_id=str(project_id),
            agent_id=agent_id,
            collection_id=collection_id,
            session_id=session_id,
            user_id=user_id,
            query=query,
            filters=filters,
            documents=governed_documents,
            duration_ms=int((time.perf_counter() - started_at) * 1000),
            status="success",
        )
        if not documents:
            return NO_RELEVANT_KNOWLEDGE_RESPONSE

        serialized = []
        for document in governed_documents:
            content = document.get(
                "content",
                document.get("content_preview", ""),
            )
            content_text = str(content)[:RAG_DOCUMENT_CHARACTER_LIMIT]
            serialized.append(
                f'<document id="{document.get("document_id", "unknown")}">'
                f"{content_text}</document>"
            )
        return (
            "<documents>"
            + "".join(serialized)
            + RAG_GROUNDING_INSTRUCTION
            + "</documents>"
        )

    return Function(
        name=tool_name,
        description=(
            f"{tool_description} Call this tool at most once per user turn; "
            "the first result is the complete governed result."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language query to search the collection.",
                }
            },
            "required": ["query"],
        },
        entrypoint=search_collection,
        skip_entrypoint_processing=True,
    )


async def create_workflow_tools(
    workflow_url: str, workflow_ids: List[str], project_id: Optional[str]
) -> List[Function]:
    """根据工作流信息生成工作流执行工具."""

    if not project_id:
        raise ValueError("project_id is required to create workflow tools")

    if not workflow_ids:
        return []

    url = workflow_url.rstrip("/")
    batch_endpoint = f"{url}/v1/workflows/batch"
    params = {"project_id": str(project_id), "workflow_ids": workflow_ids}

    async with aiohttp.ClientSession() as session:
        async with session.get(batch_endpoint, params=params) as response:
            response.raise_for_status()
            workflows_data = await response.json()

    tools = []
    for workflow_data in workflows_data:
        w_id = workflow_data.get("id")
        name = workflow_data.get("name") or f"workflow_{w_id}"
        description = workflow_data.get("description")

        tool_description = f"Execute the '{name}' workflow."
        if description:
            tool_description = f"{tool_description} Workflow description: {description}"

        # Build safe tool name
        short_id = (w_id.replace("-", "")[:8]) if w_id else "unknown"
        tool_name = f"workflow_{short_id}".lower()

        # Parse input parameters for better tool schema
        input_params = workflow_data.get("input_parameters") or []
        inputs_properties = {}
        required_inputs = []
        for param in input_params:
            p_name = param.get("name")
            p_type = param.get("type") or "string"
            p_desc = param.get("description") or ""

            # Map workflow types to JSON Schema types
            js_type = p_type
            if js_type == "number":
                js_type = "number"  # Could be number or integer in JSON schema

            inputs_properties[p_name] = {
                "type": js_type,
                "description": p_desc,
            }
            if param.get("required", True):
                required_inputs.append(p_name)

        inputs_schema = {
            "type": "object",
            "properties": inputs_properties,
            "description": "Input variables for the workflow.",
        }
        if required_inputs:
            inputs_schema["required"] = required_inputs

        def make_execute_func(wf_id: str):
            async def execute_workflow(inputs: Optional[Dict[str, Any]] = None) -> str:
                execute_endpoint = f"{url}/v1/workflows/{wf_id}/execute"
                exec_params = {"project_id": str(project_id)}
                payload = {"inputs": inputs or {}, "stream": False, "async": False}

                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            execute_endpoint,
                            params=exec_params,
                            json=payload,
                        ) as exec_response:
                            exec_response.raise_for_status()
                            data = await exec_response.json()
                            return json.dumps(data, ensure_ascii=False)
                except Exception as exc:  # noqa: BLE001
                    return f"<error>{exc}</error>"

            return execute_workflow

        tools.append(
            Function(
                name=tool_name,
                description=tool_description,
                parameters={
                    "type": "object",
                    "properties": {"inputs": inputs_schema},
                    "required": ["inputs"] if required_inputs else [],
                },
                entrypoint=make_execute_func(w_id),
                skip_entrypoint_processing=True,
            )
        )

    return tools


def create_agno_mcp_tool(
    mcp_tool: Any,
    mcp_server_url: str,
    headers: Optional[dict[str, str]] = None,
    *,
    project_id: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
) -> Function:
    """为Agno生成基于MCP协议的工具包装."""

    async def mcp_tool_entrypoint(**tool_args: Any) -> Any:
        started_at = time.perf_counter()
        try:
            async with streamablehttp_client(
                mcp_server_url, headers=headers
            ) as streams:
                read_stream, write_stream, _ = streams
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        mcp_tool.name,
                        arguments=tool_args,
                    )
                    first_content = result.content[0] if result.content else None
                    text_content = getattr(first_content, "text", None)
                    output = text_content if text_content else result
            await _record_tool_usage(
                project_id=project_id,
                agent_id=agent_id,
                tool_name=mcp_tool.name,
                session_id=session_id,
                user_id=user_id,
                arguments=tool_args,
                result=output,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                status="success",
            )
            return output
        except Exception as exc:
            await _record_tool_usage(
                project_id=project_id,
                agent_id=agent_id,
                tool_name=mcp_tool.name,
                session_id=session_id,
                user_id=user_id,
                arguments=tool_args,
                result=None,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                status="error",
                error_message=str(exc),
            )
            raise

    return Function(
        name=mcp_tool.name,
        description=mcp_tool.description,
        parameters=mcp_tool.inputSchema,
        entrypoint=mcp_tool_entrypoint,
        skip_entrypoint_processing=True,
    )


def create_plugin_tool(
    plugin_id: str,
    tool_name: str,
    title: str,
    description: Optional[str],
    parameters: Optional[Dict[str, Any]],
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Function:
    """根据插件信息生成插件工具包装."""
    from app.services.api_service import api_service_client

    async def plugin_tool_entrypoint(**tool_args: Any) -> Any:
        context = {
            "user_id": user_id,
            "session_id": session_id,
            "agent_id": agent_id,
        }
        try:
            result = await api_service_client.execute_plugin_tool(
                plugin_id=plugin_id,
                tool_name=tool_name,
                arguments=tool_args,
                context=context,
            )
            if result.get("success"):
                return result.get("content", "工具执行成功")
            else:
                return f"<error>{result.get('error', '工具执行失败')}</error>"
        except Exception as e:
            return f"<error>插件工具执行失败: {str(e)}</error>"

    return Function(
        name=tool_name,
        description=description or title,
        parameters=parameters or {"type": "object", "properties": {}},
        entrypoint=plugin_tool_entrypoint,
        skip_entrypoint_processing=True,
    )


def wrap_mcp_authenticate_tool(func: Function) -> Function:
    """捕获MCP鉴权异常并提示用户完成登录流程."""

    original = func.entrypoint

    @wraps(original)
    async def wrapped(**kwargs: Any) -> Any:
        try:
            return await original(**kwargs)
        except BaseException as exc:  # noqa: BLE001
            mcp_error = _find_first_mcp_error(exc)
            if not mcp_error:
                raise

            error_details = getattr(mcp_error, "error", None)
            if error_details and getattr(error_details, "code", None) == -32003:
                data = getattr(error_details, "data", {}) or {}
                message = data.get("message") or "Interaction required"
                if isinstance(message, dict):
                    message = message.get("text") or "Interaction required"
                url = data.get("url")
                if url:
                    message = f"{message} {url}"
                raise RuntimeError(message) from exc
            raise

    return Function.from_callable(wrapped, name=func.name, description=func.description)


def create_http_tool(
    name: str,
    description: str,
    endpoint: str,
    method: str = "POST",
    headers: Optional[Dict[str, str]] = None,
    parameters: Optional[List[Dict[str, Any]]] = None,
    timeout: float = 30.0,
) -> Function:
    """根据HTTP接口信息生成工具包装."""

    async def http_tool_entrypoint(**tool_args: Any) -> Any:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                upper_method = method.upper()
                if upper_method == "GET":
                    response = await client.get(
                        endpoint, params=tool_args, headers=headers
                    )
                elif upper_method == "POST":
                    response = await client.post(
                        endpoint, json=tool_args, headers=headers
                    )
                elif upper_method == "PUT":
                    response = await client.put(
                        endpoint, json=tool_args, headers=headers
                    )
                elif upper_method == "DELETE":
                    response = await client.delete(
                        endpoint, params=tool_args, headers=headers
                    )
                elif upper_method == "PATCH":
                    response = await client.patch(
                        endpoint, json=tool_args, headers=headers
                    )
                else:
                    return f"<error>Unsupported HTTP method: {method}</error>"

                response.raise_for_status()
                try:
                    return json.dumps(response.json(), ensure_ascii=False)
                except ValueError:
                    return response.text
        except httpx.HTTPStatusError as e:
            return f"<error>HTTP execution failed with status {e.response.status_code}: {e.response.text}</error>"
        except Exception as e:
            return f"<error>HTTP execution failed: {str(e)}</error>"

    # Convert simple parameters to JSON Schema
    properties = {}
    required = []
    if parameters:
        for p in parameters:
            p_name = p.get("name")
            if not p_name:
                continue

            p_type = p.get("type", "string")
            # Map frontend types to JSON Schema types
            js_type = p_type
            if p_type == "enum":
                js_type = "string"

            prop = {
                "type": js_type,
                "description": p.get("description", ""),
            }

            if p_type == "enum" and "enum_values" in p:
                prop["enum"] = p["enum_values"]

            properties[p_name] = prop
            if p.get("required"):
                required.append(p_name)

    return Function(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": properties,
            "required": required,
        },
        entrypoint=http_tool_entrypoint,
        skip_entrypoint_processing=True,
    )


def _find_first_mcp_error(exc: BaseException) -> Optional[McpError]:
    if isinstance(exc, McpError):
        return exc
    if isinstance(exc, ExceptionGroup):  # type: ignore[name-defined]
        for sub_exc in exc.exceptions:  # type: ignore[attr-defined]
            found = _find_first_mcp_error(sub_exc)
            if found:
                return found
    return None
