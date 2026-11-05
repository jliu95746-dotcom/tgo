"""工具运行时的辅助方法."""

from __future__ import annotations

import json
import hashlib
import logging
import time
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional
from uuid import UUID, NAMESPACE_URL, uuid5

import aiohttp
from pydantic import JsonValue, SecretStr
from agno.tools import Function
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.usage import CollectionUsageRecord, ToolUsageRecord
from app.schemas.knowledge import KnowledgeChannel
from app.schemas.business_execution_context import BusinessRequestPrincipal
from app.services.mcp_result import mcp_result_text
from app.services.http_tool_errors import http_tool_error
from app.services.plugin_result import plugin_result_text
from app.services.workflow_execution_authorization import verify_current_binding
from .mcp_authentication import (
    wrap_mcp_authenticate_tool as _wrap_mcp_authenticate_tool,
)

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
    from app.services.service_identity import rag_service_headers
    collection_endpoint = f"{url}/v1/collections/{collection_id}"
    params = {"project_id": str(project_id)}

    async with aiohttp.ClientSession(headers=rag_service_headers(url)) as session:
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
            async with aiohttp.ClientSession(headers=rag_service_headers(url)) as session:
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
    workflow_url: str, workflow_ids: List[str], project_id: Optional[str],
    *, agent_id: str, fixed_versions: Optional[Dict[str, int]] = None,
    release_ids: Optional[Dict[str, str]] = None,
    release_digests: Optional[Dict[str, str]] = None,
    request_id: str | None = None,
    business_principal: BusinessRequestPrincipal | None = None,
    business_context_token: SecretStr | None = None,
    parent_workflow_execution_id: UUID | None = None,
    workflow_lineage: tuple[str, ...] = (),
) -> List[Function]:
    """根据工作流信息生成工作流执行工具."""

    if not project_id:
        raise ValueError("project_id is required to create workflow tools")
    if not agent_id:
        raise ValueError("agent_id is required to create workflow tools")

    if not workflow_ids:
        return []
    if fixed_versions is None:
        raise ValueError("员工工作流尚未固定发布版本，请重新关联。")

    origin: dict[str, JsonValue] | None = None
    if (business_principal is None) != (business_context_token is None):
        raise ValueError("客户业务请求上下文凭证不完整")
    if business_principal is not None:
        assert business_context_token is not None
        if hashlib.sha256(
            business_context_token.get_secret_value().encode()
        ).hexdigest() != business_principal.context_digest:
            raise ValueError("客户业务请求上下文凭证不匹配")
        if str(business_principal.project_id) != str(project_id):
            raise ValueError("客户业务请求来源项目与员工项目不一致")
        if not request_id:
            raise ValueError("客户业务请求缺少 AI 执行追踪 ID")
        if business_principal.expires_at <= datetime.now(UTC):
            raise ValueError("客户业务请求来源已失效")
        origin = {
            "ai_request_id": request_id,
            "business_project_id": str(business_principal.project_id),
            "business_request_id": str(business_principal.request_id),
            "visitor_session_id": str(business_principal.visitor_session_id),
            "source_message_id": business_principal.source_message_id,
            "source_channel": business_principal.channel.value,
        }
    elif request_id:
        origin = {"ai_request_id": request_id}
    if parent_workflow_execution_id is not None and workflow_lineage:
        if origin is None:
            raise ValueError("父工作流调用缺少 AI 执行追踪 ID")
        origin["parent_execution_id"] = str(parent_workflow_execution_id)
        origin["workflow_lineage"] = list(workflow_lineage)

    url = workflow_url.rstrip("/")
    batch_endpoint = f"{url}/v1/workflows/batch"
    params = {"project_id": str(project_id), "workflow_ids": workflow_ids}

    async with aiohttp.ClientSession() as session:
        async with session.get(batch_endpoint, params=params) as response:
            response.raise_for_status()
            workflows_data = await response.json()

        found_ids = {row.get("id") for row in workflows_data}
        if found_ids != set(workflow_ids):
            raise ValueError("员工关联的工作流已不存在或不属于当前项目。")

        if fixed_versions is not None:
            for workflow_data in workflows_data:
                workflow_id = workflow_data["id"]
                current_published = workflow_data.get("published_version")
                if (workflow_data.get("status") != "active"
                        or not isinstance(current_published, int)
                        or isinstance(current_published, bool)
                        or current_published < 1):
                    raise ValueError("请先发布所选工作流，再关联为员工业务能力。")
                version = fixed_versions.get(workflow_id)
                expected_release_id = (release_ids or {}).get(workflow_id)
                expected_digest = (release_digests or {}).get(workflow_id)
                if not isinstance(version, int) or isinstance(version, bool) or version < 1 or not expected_release_id or not expected_digest:
                    raise ValueError("员工工作流尚未固定发布版本，请重新关联。")
                endpoint = (
                    f"{url}/v1/workflows/{workflow_id}/releases/"
                    f"{version}/integration"
                )
                async with session.get(
                    endpoint, params={"project_id": str(project_id)}
                ) as response:
                    response.raise_for_status()
                    integration = await response.json()
                if (
                    integration.get("workflow_id") != workflow_id
                    or integration.get("release_version") != version
                    or integration.get("release_id") != expected_release_id
                    or integration.get("definition_digest") != expected_digest
                ):
                    raise ValueError("员工工作流发布版本校验失败，请重新关联。")
                workflow_data["pinned_release"] = integration

    tools = []
    for workflow_data in workflows_data:
        w_id = workflow_data.get("id")
        pinned = workflow_data.get("pinned_release")
        release_version = pinned["release_version"]
        if workflow_data.get("status") != "active" or not isinstance(release_version, int) or isinstance(release_version, bool) or release_version < 1:
            raise ValueError("请先发布所选工作流，再关联为员工业务能力。")
        name = workflow_data.get("name") or f"workflow_{w_id}"
        description = workflow_data.get("description")

        tool_description = f"Execute the '{name}' workflow."
        if description:
            tool_description = f"{tool_description} Workflow description: {description}"
        requires_approval = "approval" in (pinned.get("node_types") or [])
        if requires_approval:
            if not request_id:
                raise ValueError("人工审批工作流缺少 AI 执行追踪 ID")
            tool_description += (
                " Submits for human approval; do not claim the task is complete."
            )

        # Build safe tool name
        short_id = (w_id.replace("-", "")[:8]) if w_id else "unknown"
        tool_name = f"workflow_{short_id}".lower()

        # Parse input parameters for better tool schema
        input_params = pinned.get("input_parameters") or []
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

        def make_execute_func(
            wf_id: str, fixed_version: int, release_id: str,
            release_digest: str, approval: bool,
        ) -> Callable[[Optional[Dict[str, Any]]], Awaitable[str]]:
            async def execute_workflow(inputs: Optional[Dict[str, Any]] = None) -> str:
                execute_endpoint = f"{url}/v1/workflows/{wf_id}/execute"
                exec_params = {"project_id": str(project_id)}
                payload = {
                    "inputs": inputs or {}, "stream": False, "async": approval,
                    "execution_mode": "production", "release_version": fixed_version,
                    "expected_release_id": release_id,
                    "expected_release_digest": release_digest,
                    "employee_agent_id": agent_id,
                }
                if origin is not None:
                    payload["origin"] = origin
                if approval:
                    payload["invocation_key"] = str(uuid5(
                        NAMESPACE_URL,
                        f"workflow-approval:{project_id}:{agent_id}:{wf_id}:"
                        f"{request_id}:"
                        + json.dumps(inputs or {}, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")),
                    ))

                try:
                    if (business_principal is not None
                            and business_principal.expires_at <= datetime.now(UTC)):
                        raise PermissionError("客户业务请求来源已失效")
                    await verify_current_binding(
                        agent_id, str(project_id), wf_id, fixed_version,
                        release_id, release_digest,
                    )
                    headers = {"X-Internal-API-Key": settings.secret_key}
                    from app.services.quota_authorization import (
                        current_authorization,
                    )
                    usage = current_authorization.get()
                    if usage is not None:
                        if str(usage.project_id) != str(project_id):
                            raise PermissionError("工作流额度与员工企业不一致")
                        if (usage.reservation_id is None
                                or usage.lease_id is None):
                            raise PermissionError("工作流额度来源不完整")
                        headers.update({
                            "X-AI-Reservation": str(usage.reservation_id),
                            "X-AI-Lease": str(usage.lease_id),
                        })
                    if business_principal is not None and business_context_token is not None:
                        headers.update({
                            "X-Business-Request-Context": business_context_token.get_secret_value(),
                            "X-Business-Visitor-Id": str(business_principal.visitor_id),
                            "X-Business-Request-Fingerprint": business_principal.request_fingerprint,
                            "X-Execution-Channel": business_principal.channel.value,
                        })
                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            execute_endpoint,
                            params=exec_params,
                            json=payload,
                            headers=headers,
                        ) as exec_response:
                            exec_response.raise_for_status()
                            data = await exec_response.json()
                            if approval:
                                if (not isinstance(data, dict)
                                        or not isinstance(data.get("id"), str)
                                        or not isinstance(data.get("status"), str)):
                                    raise ValueError("人工审批工作流返回的执行记录无效")
                                return json.dumps({
                                    "success": data["status"] not in {
                                        "failed", "cancelled",
                                    },
                                    "status": data["status"],
                                    "execution_id": data["id"],
                                }, ensure_ascii=False)
                            return json.dumps(data, ensure_ascii=False)
                except PermissionError:
                    return json.dumps({
                        "success": False,
                        "error": "员工工作流授权已变更，请刷新配置或转人工处理。",
                    }, ensure_ascii=False)
                except Exception:  # noqa: BLE001
                    return json.dumps({"success": False, "error": "工作流执行失败，请转人工处理。"}, ensure_ascii=False)

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
                entrypoint=make_execute_func(
                    w_id, release_version, pinned["release_id"],
                    pinned["definition_digest"], requires_approval,
                ),
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
    headers_factory: Callable[[], dict[str, str]] | None = None,
) -> Function:
    """为Agno生成基于MCP协议的工具包装."""

    async def mcp_tool_entrypoint(**tool_args: Any) -> Any:
        started_at = time.perf_counter()
        try:
            async with streamablehttp_client(
                mcp_server_url, headers=headers_factory() if headers_factory else headers
            ) as streams:
                read_stream, write_stream, _ = streams
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        mcp_tool.name,
                        arguments=tool_args,
                    )
            output = mcp_result_text(result)
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
    project_id: Optional[str] = None,
) -> Function:
    """根据插件信息生成插件工具包装."""
    from app.services.api_service import api_service_client
    from app.schemas.plugin_execution import PluginExecutionContext

    async def plugin_tool_entrypoint(**tool_args: Any) -> str:
        context = PluginExecutionContext(
            visitor_id=PluginExecutionContext.visitor_from_user(user_id),
            session_id=session_id,
            agent_id=agent_id,
        ).model_dump(mode="json")
        result = await api_service_client.execute_plugin_tool(
            plugin_id=plugin_id,
            tool_name=tool_name,
            arguments=tool_args,
            context=context,
            project_id=project_id,
        )
        # Let the agent framework mark failed executions as failures, rather
        # than treating an error string as a successfully completed tool call.
        return plugin_result_text(result)

    return Function(
        name=tool_name,
        description=description or title,
        parameters=parameters or {"type": "object", "properties": {}},
        entrypoint=plugin_tool_entrypoint,
        skip_entrypoint_processing=True,
    )


def wrap_mcp_authenticate_tool(func: Function) -> Function:
    """Keep the existing import path for MCP tool builders."""
    return _wrap_mcp_authenticate_tool(func)


def create_http_tool(
    name: str,
    description: str,
    endpoint: str,
    method: str = "POST",
    headers: Optional[Dict[str, str]] = None,
    parameters: Optional[List[Dict[str, Any]]] = None,
    timeout: float = 30.0,
    logistics_provider: object = None,
) -> Function:
    """根据HTTP接口信息生成工具包装."""

    async def http_tool_entrypoint(**tool_args: Any) -> Any:
        import httpx

        try:
            if logistics_provider:
                from app.services.logistics_provider import execute_logistics_provider
                try:
                    result = await execute_logistics_provider(endpoint, logistics_provider, tool_args)
                    return json.dumps(result, ensure_ascii=False)
                except ValueError:
                    return "<error>快递服务商查询失败：请检查单号、授权、额度和返回字段配置</error>"
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
        except Exception as e:
            return http_tool_error(e)

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
