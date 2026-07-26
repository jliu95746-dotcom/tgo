"""Chat service for handling chat completion business logic."""

import asyncio
import hashlib
import json
from datetime import datetime
from typing import Any, AsyncIterator, Dict, Optional
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

import app.services.visitor_service as visitor_service
from app.core.logging import get_logger
from app.models import Platform, Project, Staff, Visitor, VisitorServiceStatus
from app.schemas.chat import (
    OpenAIChatCompletionChoice,
    OpenAIChatCompletionResponse,
    OpenAIChatCompletionUsage,
    OpenAIChatMessage,
)
from app.services.ai_client import AIServiceClient
from app.services.wukongim_client import wukongim_client
from app.utils.const import MessageType

logger = get_logger("services.chat")

ai_client = AIServiceClient()
background_ai_tasks: set[asyncio.Task[None]] = set()

# ============================================================================
# Validation & Helpers
# ============================================================================


def validate_platform_and_project(
    platform_api_key: str, db: Session
) -> tuple[Platform, Project]:
    """Validate Platform API key and return platform with project."""
    platform = (
        db.query(Platform)
        .filter(
            Platform.api_key == platform_api_key,
            Platform.is_active.is_(True),
            Platform.deleted_at.is_(None),
        )
        .first()
    )
    if not platform:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
        )

    project = platform.project
    if not project or not project.api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Platform is not linked to a valid project",
        )

    return platform, project


def resolve_service_mode(platform: Platform, visitor: Optional[Visitor]) -> str:
    """Resolve the effective service mode while preserving legacy fields."""
    if visitor is not None:
        explicit_mode = getattr(visitor, "service_mode", None)
        visitor_ai_disabled = getattr(visitor, "ai_disabled", None)
        if isinstance(explicit_mode, str) and explicit_mode in {
            "auto",
            "assist",
            "manual",
        }:
            # Existing handoff code still sets ai_disabled directly. Preserve
            # that safety override even when a previous explicit mode was auto.
            if explicit_mode == "auto" and visitor_ai_disabled is True:
                return "manual"
            return explicit_mode
        if visitor_ai_disabled is not None:
            return "manual" if visitor_ai_disabled else "auto"

    platform_mode = getattr(platform, "ai_mode", None)
    if platform_mode == "assist":
        return "assist"
    if platform_mode == "auto":
        return "auto"
    return "manual"


def is_ai_disabled(platform: Platform, visitor: Optional[Visitor]) -> bool:
    """Return whether customer-facing AI auto-send must be blocked."""
    return resolve_service_mode(platform, visitor) != "auto"


def sse_format(event: Dict[str, Any]) -> str:
    """Format event as SSE message."""
    event_type = event.get("event_type") or "message"
    data = json.dumps(event, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"


def authenticate_staff_or_platform(
    db: Session,
    credentials: Optional[HTTPAuthorizationCredentials] = None,
    platform_api_key: Optional[str] = None,
) -> tuple[Optional[Staff], Optional[Platform]]:
    """Authenticate via JWT (staff) or platform API key."""
    current_user: Optional[Staff] = None
    platform: Optional[Platform] = None

    if credentials and credentials.credentials:
        from app.core.security import verify_token

        payload = verify_token(credentials.credentials)
        if payload:
            username = payload.get("sub")
            if username:
                current_user = (
                    db.query(Staff)
                    .filter(Staff.username == username, Staff.deleted_at.is_(None))
                    .first()
                )

    if not current_user and platform_api_key:
        platform = (
            db.query(Platform)
            .filter(
                Platform.api_key == platform_api_key,
                Platform.is_active.is_(True),
                Platform.deleted_at.is_(None),
            )
            .first()
        )

    return current_user, platform


# ============================================================================
# AI Integration Logic
# ============================================================================


def _extract_ai_content_chunk(event_data: Dict[str, Any]) -> Optional[str]:
    """Extract text from the supported AI event envelopes."""

    data = event_data.get("data") or {}
    if not isinstance(data, dict):
        return None

    chunk_text = data.get("content_chunk") or data.get("content") or data.get("text")
    if not chunk_text:
        inner_data = data.get("data", {})
        if isinstance(inner_data, dict):
            chunk_text = (
                inner_data.get("content_chunk")
                or inner_data.get("content")
                or inner_data.get("text")
            )
    if chunk_text is None:
        return None
    chunk = str(chunk_text)
    return chunk or None


async def forward_ai_event_to_wukongim(
    event_type: str,
    event_data: Dict[str, Any],
    channel_id: str,
    channel_type: int,
    client_msg_no: str,
    from_uid: str,
) -> Optional[str]:
    """Forward AI event to WuKongIM using the new Stream API.

    Flow:
      agent_execution_started  → send_stream_message (anchor with is_stream=1)
      agent_content_chunk      → send_stream_event (stream.delta)
      workflow_completed / agent_response_complete → close + finish
      workflow_failed          → send_stream_event (stream.error)
    """
    try:
        data = event_data.get("data") or {}
        logger.info(f"Forwarding AI event {event_type} to WuKongIM: {data}")

        if event_type == "agent_execution_started":
            # Send stream anchor message
            await wukongim_client.send_stream_message(
                from_uid=from_uid,
                channel_id=channel_id,
                channel_type=channel_type,
                client_msg_no=client_msg_no,
                payload={"type": 100, "content": "AI 正在思考中..."},
            )

        elif event_type == "agent_content_chunk":
            chunk_str = _extract_ai_content_chunk(event_data)
            if chunk_str:
                await wukongim_client.send_stream_event(
                    channel_id=channel_id,
                    channel_type=channel_type,
                    client_msg_no=client_msg_no,
                    event_id=uuid4().hex,
                    event_type="stream.delta",
                    event_key="main",
                    from_uid=from_uid,
                    payload={"kind": "text", "delta": chunk_str},
                )
                return chunk_str

        elif event_type in {"workflow_completed", "agent_response_complete"}:
            final_content = data.get("final_content")
            total_chunks = data.get("total_chunks")
            fallback_content: Optional[str] = None
            if isinstance(final_content, str) and final_content and total_chunks == 0:
                fallback_content = final_content
                await wukongim_client.send_stream_event(
                    channel_id=channel_id,
                    channel_type=channel_type,
                    client_msg_no=client_msg_no,
                    event_id=uuid4().hex,
                    event_type="stream.delta",
                    event_key="main",
                    from_uid=from_uid,
                    payload={"kind": "text", "delta": fallback_content},
                )

            # Close the stream channel, then finish the entire message
            await wukongim_client.send_stream_event(
                channel_id=channel_id,
                channel_type=channel_type,
                client_msg_no=client_msg_no,
                event_id=uuid4().hex,
                event_type="stream.close",
                event_key="main",
                from_uid=from_uid,
            )
            await wukongim_client.send_stream_event(
                channel_id=channel_id,
                channel_type=channel_type,
                client_msg_no=client_msg_no,
                event_id=uuid4().hex,
                event_type="stream.finish",
                event_key="main",
                from_uid=from_uid,
            )
            return fallback_content

        elif event_type == "workflow_failed":
            error_message = (
                data.get("error") or data.get("error_message") or "AI processing failed"
            )
            await wukongim_client.send_stream_event(
                channel_id=channel_id,
                channel_type=channel_type,
                client_msg_no=client_msg_no,
                event_id=uuid4().hex,
                event_type="stream.error",
                event_key="main",
                from_uid=from_uid,
                payload={"error": str(error_message)},
            )
            for terminal_event_type in ("stream.close", "stream.finish"):
                await wukongim_client.send_stream_event(
                    channel_id=channel_id,
                    channel_type=channel_type,
                    client_msg_no=client_msg_no,
                    event_id=uuid4().hex,
                    event_type=terminal_event_type,
                    event_key="main",
                    from_uid=from_uid,
                )

    except Exception as e:
        logger.error(f"Failed to forward AI event {event_type} to WuKongIM: {e}")
    return None


async def process_ai_stream_to_wukongim(
    project_id: str,
    user_id: str,
    message: str,
    channel_id: str,
    channel_type: int,
    client_msg_no: str,
    from_uid: str,
    session_id: Optional[str] = None,
    system_message: Optional[str] = None,
    expected_output: Optional[str] = None,
    agent_id: Optional[str] = None,
    knowledge_channel: Optional[str] = None,
    excluded_tool_ids: tuple[str, ...] = (),
) -> AsyncIterator[Dict[str, Any]]:
    """Process AI stream and forward events to WuKongIM, while yielding events for SSE."""
    full_content = ""
    stream_finished = False

    # Give the visitor immediate feedback before remote tool discovery/model setup.
    anchor_task = asyncio.create_task(
        forward_ai_event_to_wukongim(
            event_type="agent_execution_started",
            event_data={"data": {}},
            channel_id=channel_id,
            channel_type=channel_type,
            client_msg_no=client_msg_no,
            from_uid=from_uid,
        )
    )
    await asyncio.sleep(0)

    # 2) Run AI completion
    try:
        async for stream_event_type, data in ai_client.run_supervisor_agent_stream(
            project_id=project_id,
            agent_id=agent_id,
            user_id=user_id,
            message=message,
            session_id=session_id,
            enable_memory=True,
            system_message=system_message,
            expected_output=expected_output,
            knowledge_channel=knowledge_channel,
            excluded_tool_ids=list(excluded_tool_ids),
        ):
            event_type = data.get("event_type") if isinstance(data, dict) else None
            if not event_type:
                event_type = stream_event_type
            # WuKongIM HTTP calls can take over a second. Buffer provider token
            # chunks and publish one delta at completion instead of blocking the
            # AI stream once per token.
            if event_type == "agent_content_chunk":
                chunk = _extract_ai_content_chunk(data)
                if chunk:
                    full_content += chunk
            else:
                if event_type == "agent_tool_call_started":
                    full_content = ""
                if event_type == "agent_execution_started":
                    yield {"event_type": event_type, "data": data}
                    continue
                if (
                    event_type in {"workflow_completed", "agent_response_complete"}
                    and stream_finished
                ):
                    yield {"event_type": event_type, "data": data}
                    continue
                event_to_forward = data
                if event_type in {
                    "workflow_completed",
                    "agent_response_complete",
                } and isinstance(data, dict):
                    completion_data = data.get("data")
                    if isinstance(completion_data, dict):
                        provider_final = completion_data.get("final_content")
                        if not full_content and isinstance(provider_final, str):
                            full_content = provider_final
                        completion_data = {
                            **completion_data,
                            "final_content": full_content,
                            "total_chunks": 0,
                        }
                        event_to_forward = {**data, "data": completion_data}
                    await anchor_task
                await forward_ai_event_to_wukongim(
                    event_type=event_type,
                    event_data=event_to_forward,
                    channel_id=channel_id,
                    channel_type=channel_type,
                    client_msg_no=client_msg_no,
                    from_uid=from_uid,
                )
                if event_type in {"workflow_completed", "agent_response_complete"}:
                    stream_finished = True

            # Yield for SSE
            yield {"event_type": event_type, "data": data}

    except Exception as e:
        logger.error(f"Error in AI stream processing: {e}")
        error_data = {"error_message": str(e)}
        await anchor_task
        await forward_ai_event_to_wukongim(
            event_type="workflow_failed",
            event_data={"data": error_data},
            channel_id=channel_id,
            channel_type=channel_type,
            client_msg_no=client_msg_no,
            from_uid=from_uid,
        )
        yield {"event_type": "workflow_failed", "data": error_data}


async def handle_ai_response_non_stream(
    project_id: str,
    visitor_id: str,
    message: str,
    channel_id: str,
    channel_type: int,
    client_msg_no: str,
    from_uid: str,
    session_id: Optional[str] = None,
    system_message: Optional[str] = None,
    expected_output: Optional[str] = None,
    agent_id: Optional[str] = None,
    knowledge_channel: Optional[str] = None,
    excluded_tool_ids: tuple[str, ...] = (),
) -> Dict[str, Any]:
    """Handle AI completion in a non-streaming way, while still forwarding to WuKongIM."""
    full_content = ""
    last_data = {}
    stream_finished = False

    anchor_task = asyncio.create_task(
        forward_ai_event_to_wukongim(
            event_type="agent_execution_started",
            event_data={"data": {}},
            channel_id=channel_id,
            channel_type=channel_type,
            client_msg_no=client_msg_no,
            from_uid=from_uid,
        )
    )
    await asyncio.sleep(0)

    try:
        async for stream_event_type, data in ai_client.run_supervisor_agent_stream(
            project_id=project_id,
            agent_id=agent_id,
            user_id=visitor_id,
            message=message,
            session_id=session_id,
            enable_memory=True,
            system_message=system_message,
            expected_output=expected_output,
            knowledge_channel=knowledge_channel,
            excluded_tool_ids=list(excluded_tool_ids),
        ):
            event_type = data.get("event_type") if isinstance(data, dict) else None
            if not event_type:
                event_type = stream_event_type
            if event_type == "agent_content_chunk":
                chunk = _extract_ai_content_chunk(data)
                if chunk:
                    full_content += chunk
            else:
                if event_type == "agent_tool_call_started":
                    full_content = ""
                if event_type == "agent_execution_started":
                    last_data = data
                    continue
                if (
                    event_type in {"workflow_completed", "agent_response_complete"}
                    and stream_finished
                ):
                    last_data = data
                    continue
                event_to_forward = data
                if event_type in {
                    "workflow_completed",
                    "agent_response_complete",
                } and isinstance(data, dict):
                    completion_data = data.get("data")
                    if isinstance(completion_data, dict):
                        provider_final = completion_data.get("final_content")
                        if not full_content and isinstance(provider_final, str):
                            full_content = provider_final
                        event_to_forward = {
                            **data,
                            "data": {
                                **completion_data,
                                "final_content": full_content,
                                "total_chunks": 0,
                            },
                        }
                    await anchor_task
                await forward_ai_event_to_wukongim(
                    event_type=event_type,
                    event_data=event_to_forward,
                    channel_id=channel_id,
                    channel_type=channel_type,
                    client_msg_no=client_msg_no,
                    from_uid=from_uid,
                )
                if event_type in {"workflow_completed", "agent_response_complete"}:
                    stream_finished = True
            last_data = data

        return {"success": True, "content": full_content, "data": last_data}
    except Exception as e:
        logger.error(f"Error in non-stream AI processing: {e}")
        error_data = {"error_message": str(e)}
        await anchor_task
        await forward_ai_event_to_wukongim(
            event_type="workflow_failed",
            event_data={"data": error_data},
            channel_id=channel_id,
            channel_type=channel_type,
            client_msg_no=client_msg_no,
            from_uid=from_uid,
        )
        return {"success": False, "error": str(e)}


async def run_background_ai_interaction(
    project_id: str,
    user_id: str,
    message: str,
    channel_id: str,
    channel_type: int,
    client_msg_no: str,
    from_uid: str,
    session_id: Optional[str] = None,
    system_message: Optional[str] = None,
    expected_output: Optional[str] = None,
    agent_id: Optional[str] = None,
    knowledge_channel: Optional[str] = None,
    excluded_tool_ids: tuple[str, ...] = (),
    started_event: Optional[asyncio.Event] = None,
) -> None:
    """Run AI interaction in the background.

    Args:
        started_event: Optional asyncio.Event that will be set when agent execution starts.
    """
    async for event_payload in process_ai_stream_to_wukongim(
        project_id=project_id,
        user_id=user_id,
        message=message,
        channel_id=channel_id,
        channel_type=channel_type,
        client_msg_no=client_msg_no,
        from_uid=from_uid,
        session_id=session_id,
        system_message=system_message,
        expected_output=expected_output,
        agent_id=agent_id,
        knowledge_channel=knowledge_channel,
        excluded_tool_ids=excluded_tool_ids,
    ):
        # Signal that AI processing has started
        if started_event and not started_event.is_set():
            event_type = event_payload.get("event_type")
            if event_type == "agent_execution_started":
                started_event.set()


def schedule_background_ai_interaction(
    *,
    project_id: str,
    user_id: str,
    message: str,
    channel_id: str,
    channel_type: int,
    client_msg_no: str,
    from_uid: str,
    session_id: Optional[str] = None,
    system_message: Optional[str] = None,
    expected_output: Optional[str] = None,
    agent_id: Optional[str] = None,
    knowledge_channel: Optional[str] = None,
    excluded_tool_ids: tuple[str, ...] = (),
    interaction_run_id: UUID | None = None,
) -> asyncio.Task[None]:
    """Schedule an AI run and retain it until completion."""

    task = asyncio.create_task(
        run_background_ai_interaction(
            project_id=project_id,
            user_id=user_id,
            message=message,
            channel_id=channel_id,
            channel_type=channel_type,
            client_msg_no=client_msg_no,
            from_uid=from_uid,
            session_id=session_id,
            system_message=system_message,
            expected_output=expected_output,
            agent_id=agent_id,
            knowledge_channel=knowledge_channel,
            excluded_tool_ids=excluded_tool_ids,
        )
    )
    background_ai_tasks.add(task)

    def _release(completed: asyncio.Task[None]) -> None:
        background_ai_tasks.discard(completed)
        if completed.cancelled():
            logger.warning(
                "Background AI interaction was cancelled",
                extra={"client_msg_no": client_msg_no},
            )
            if interaction_run_id is not None:
                from app.services.ai_interaction_run_service import (
                    mark_ai_interaction_finished,
                )

                mark_ai_interaction_finished(
                    interaction_run_id,
                    error_message="Background AI interaction was cancelled",
                )
            return
        error = completed.exception()
        if error is not None:
            logger.error(
                "Background AI interaction failed",
                exc_info=error,
                extra={"client_msg_no": client_msg_no},
            )
        if interaction_run_id is not None:
            from app.services.ai_interaction_run_service import (
                mark_ai_interaction_finished,
            )

            mark_ai_interaction_finished(
                interaction_run_id,
                error_message=str(error) if error is not None else None,
            )

    task.add_done_callback(_release)
    return task


# ============================================================================
# UI User Action Handling
# ============================================================================


def convert_ui_user_action_to_query(user_action: Dict[str, Any]) -> str:
    """Convert a UI userAction payload into a natural-language query.

    The frontend sends ``{ "actionName": "...", "context": {...} }``
    when the user interacts with an interactive UI component.
    We translate this into a human-readable message so the LLM Agent
    can respond naturally (like the restaurant_finder sample does).
    """
    action_name = user_action.get("actionName", "unknown_action")
    context = user_action.get("context", {})

    context_parts = [f"{k}={v}" for k, v in context.items() if v]
    context_str = ", ".join(context_parts) if context_parts else "no additional context"

    return (
        f"[UI Action] User triggered action '{action_name}' with context: {context_str}"
    )


# ============================================================================
# OpenAI Mapping Helpers
# ============================================================================


def extract_messages_from_openai_format(
    messages: list[OpenAIChatMessage], user_field: Optional[str] = None
) -> tuple[str, Optional[str], str]:
    """Extract user message, system message, and platform_open_id from OpenAI message format."""
    user_message = None
    system_message = None

    for msg in reversed(messages):
        if msg.role == "user" and user_message is None:
            user_message = msg.content
        elif msg.role == "system" and system_message is None:
            system_message = msg.content

    if not user_message:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No user message found in messages array",
        )

    platform_open_id = user_field or f"openai_user_{uuid4().hex[:8]}"

    return user_message, system_message, platform_open_id


def estimate_token_usage(
    messages: list[OpenAIChatMessage], completion_text: str
) -> tuple[int, int, int]:
    """Estimate token usage for prompt and completion."""
    prompt_text = " ".join([msg.content for msg in messages])
    prompt_tokens = len(prompt_text.split())
    completion_tokens = len(completion_text.split())
    total_tokens = prompt_tokens + completion_tokens

    return prompt_tokens, completion_tokens, total_tokens


def build_openai_completion_response(
    completion_id: str,
    created_timestamp: int,
    model: str,
    completion_text: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
) -> OpenAIChatCompletionResponse:
    """Build OpenAI-compatible completion response."""
    return OpenAIChatCompletionResponse(
        id=completion_id,
        object="chat.completion",
        created=created_timestamp,
        model=model,
        choices=[
            OpenAIChatCompletionChoice(
                index=0,
                message=OpenAIChatMessage(
                    role="assistant",
                    content=completion_text,
                ),
                finish_reason="stop",
            )
        ],
        usage=OpenAIChatCompletionUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        ),
    )


# ============================================================================
# Messaging Helpers
# ============================================================================


async def send_user_message_to_wukongim(
    *,
    from_uid: str,
    channel_id: str,
    channel_type: int,
    content: str,
    msg_type: Optional[MessageType] = MessageType.TEXT,
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Send a copy of the user's message to WuKongIM (best-effort)."""
    if not content:
        return None
    source_message_id = extra.get("message_id") if extra else None
    if isinstance(source_message_id, str) and source_message_id:
        correlation_source = f"{channel_id}:{from_uid}:{source_message_id}"
        correlation_hash = hashlib.sha256(
            correlation_source.encode("utf-8")
        ).hexdigest()
        client_msg_no = f"platform_{correlation_hash[:32]}"
    else:
        client_msg_no = f"user_{uuid4().hex}"
    try:
        # Build payload based on msg_type
        # 1=TEXT, 2=IMAGE, 3=FILE
        payload: Dict[str, Any] = {
            "type": int(msg_type or MessageType.TEXT),
            "content": content,
        }
        if msg_type == MessageType.IMAGE:
            payload["url"] = content
        elif msg_type == MessageType.FILE:
            payload["url"] = content
            # For files, name is often required by frontend
            if extra and extra.get("file_name"):
                payload["name"] = extra["file_name"]
            else:
                payload["name"] = content.split("/")[-1]
        if extra:
            payload["extra"] = extra

        await wukongim_client.send_message(
            payload=payload,
            from_uid=from_uid,
            channel_id=channel_id,
            channel_type=channel_type,
            client_msg_no=client_msg_no,
        )
    except Exception:
        # Do not fail main flow on WuKongIM send failure
        return client_msg_no
    return client_msg_no


# ============================================================================
# Visitor & Queue Management
# ============================================================================


async def get_or_create_visitor(
    db: Session,
    platform: Platform,
    platform_open_id: str,
    nickname: Optional[str] = None,
    avatar_url: Optional[str] = None,
) -> tuple[Visitor, bool]:
    """
    获取或创建访客。

    如果访客存在且信息发生变化，自动更新并通知 WuKongIM。

    Args:
        db: 数据库会话
        platform: 平台对象
        platform_open_id: 平台用户ID
        nickname: 昵称（可选）
        avatar_url: 头像URL（可选）

    Returns:
        tuple[Visitor, bool]: (访客对象, 是否发生了更新)
    """
    visitor = (
        db.query(Visitor)
        .filter(
            Visitor.platform_id == platform.id,
            Visitor.platform_open_id == platform_open_id,
            Visitor.deleted_at.is_(None),
        )
        .first()
    )

    if not visitor:
        # 创建新访客
        visitor = await visitor_service.create_visitor_with_channel(
            db=db,
            platform=platform,
            platform_open_id=platform_open_id,
            name=nickname,  # 同时设置 name
            nickname=nickname,
            avatar_url=avatar_url,
        )
        return visitor, True
    else:
        # 更新访客信息（如果提供且发生变化）
        changed = False
        if nickname:
            if visitor.nickname != nickname:
                visitor.nickname = nickname
                changed = True
            if visitor.name != nickname:
                visitor.name = nickname
                changed = True
            # 同步更新 nickname_zh 以确保两个字段一致
            if visitor.nickname_zh != nickname:
                visitor.nickname_zh = nickname
                changed = True

        if avatar_url and visitor.avatar_url != avatar_url:
            visitor.avatar_url = avatar_url
            changed = True

        # 重置已关闭的访客状态
        if visitor.service_status == VisitorServiceStatus.CLOSED.value:
            visitor.service_status = VisitorServiceStatus.NEW.value
            changed = True
            logger.debug(f"Reset visitor {visitor.id} status from CLOSED to NEW")

        if changed:
            visitor.updated_at = datetime.utcnow()
            db.commit()

    return visitor, changed
