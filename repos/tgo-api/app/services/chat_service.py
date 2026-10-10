"""Chat service for handling chat completion business logic."""

import asyncio
import hashlib
import json
from contextlib import aclosing
from datetime import datetime
from typing import Any, AsyncGenerator, AsyncIterator, Dict, Optional
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
from app.schemas.ai_runs import ReplyRun, SupervisorCancelResponse
from app.services.ai_reply_control import (
    ReplyEvent, ReplyStopped, begin_reply_publication, controlled_reply, tracked_ai_stream,
)
from app.services.humanization_service import (
    ASSIST_FACT_GATHERING_PROMPT, get_humanization_skill_prompt,
    recent_customer_messages, rewrite_assist_draft,
)
from app.schemas.knowledge_evidence import (
    BusinessToolEvidence, KnowledgeEvidence,
)
from app.services.current_knowledge import read_knowledge_evidence
from app.schemas.chat_media import ChatMediaInput, MediaModelOptions
from app.services.chat_media_analysis import prepare_chat_media
from app.services.chat_media_service import MediaInputError
from app.services.wukongim_client import wukongim_client
from app.services.reply_service_mode import ensure_customer_auto_reply
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
        from app.core.security import resolve_staff_token

        current_user = resolve_staff_token(db, credentials.credentials)

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
                payload={"type": 100, "content": ""},
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
            from app.services.ai_usage_runtime import begin_delivery, confirm_im_delivery, current_permit
            if current_permit() is not None:
                if data.get("success") is False or not isinstance(final_content, str) or not final_content.strip():
                    raise RuntimeError("Cannot bill an unsuccessful or empty reply")
                if not wukongim_client.enabled:
                    raise RuntimeError("Message delivery is unavailable")
                await begin_delivery()
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
            await confirm_im_delivery()
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
        from app.services.ai_usage_runtime import current_permit
        if current_permit() is not None:
            raise
    return None


async def process_ai_stream_to_wukongim(
    project_id: str, user_id: str, message: str, channel_id: str,
    channel_type: int, client_msg_no: str, from_uid: str,
    session_id: Optional[str] = None, system_message: Optional[str] = None,
    expected_output: Optional[str] = None, agent_id: Optional[str] = None,
    knowledge_channel: Optional[str] = None, excluded_tool_ids: tuple[str, ...] = (),
    humanization_skill_name: str | None = None,
    media_input: ChatMediaInput | None = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Own the reply before exposing its first stream anchor to any client."""
    identity = ReplyRun(
        project_id=project_id, client_msg_no=client_msg_no,
        channel_id=channel_id, channel_type=channel_type,
    )

    async def publish_error(error: str) -> None:
        await forward_ai_event_to_wukongim(
            event_type="workflow_failed", event_data={"data": {"error_message": error}},
            channel_id=channel_id, channel_type=channel_type,
            client_msg_no=client_msg_no, from_uid=from_uid,
        )

    async def stop_upstream(run_id: str) -> bool:
        raw = await ai_client.cancel_supervisor_run(project_id, run_id)
        receipt = SupervisorCancelResponse.model_validate(raw)
        return receipt.run_id == run_id and receipt.cancelled

    async def source() -> AsyncGenerator[ReplyEvent, None]:
        from app.services.ai_usage_runtime import metered_reply
        from app.core.exceptions import TGOAPIException
        try:
            async with metered_reply(project_id, client_msg_no, channel_id, channel_type):
                from app.services.ai_usage_intent import route_reply_intent
                routed_system = system_message
                routed_excluded = excluded_tool_ids
                routed_evidence: tuple[BusinessToolEvidence, ...] = ()
                if channel_type == 251 and media_input is None:
                    outcome = await route_reply_intent(project_id, client_msg_no, message)
                    if outcome is not None:
                        if outcome.routing_target == "human_handoff":
                            yield {"event_type": "human_handoff", "data": {"message": "该问题需要人工客服处理，已停止 AI 自动回复。"}}
                            return
                        context = "本轮只询问一个必要的澄清问题，不得猜测订单号或客户意图。" if outcome.routing_target == "clarify" else outcome.tool_context
                        if context:
                            routed_system = (system_message or "") + "\n" + context
                            routed_evidence = (BusinessToolEvidence(
                                name=outcome.routing_target, content=context,
                                success=True,
                            ),)
                        routed_excluded = tuple(set(excluded_tool_ids) | set(outcome.excluded_tool_ids))
                async for event in _process_ai_reply_to_wukongim(
            project_id=project_id, user_id=user_id, message=message,
            channel_id=channel_id, channel_type=channel_type,
            client_msg_no=client_msg_no, from_uid=from_uid,
            session_id=session_id, system_message=routed_system,
            expected_output=expected_output, agent_id=agent_id,
            knowledge_channel=knowledge_channel, excluded_tool_ids=routed_excluded,
                    humanization_skill_name=humanization_skill_name, media_input=media_input,
                    trusted_tool_evidence=routed_evidence,
                ):
                    yield event
        except TGOAPIException as exc:
            yield {"event_type": "workflow_failed", "data": {"error_message": exc.message, "code": exc.code}}
    async with aclosing(controlled_reply(identity, source, publish_error, stop_upstream)) as events:
        async for event in events:
            yield event


async def _process_ai_reply_to_wukongim(
    project_id: str, user_id: str, message: str, channel_id: str,
    channel_type: int, client_msg_no: str, from_uid: str,
    session_id: Optional[str] = None, system_message: Optional[str] = None,
    expected_output: Optional[str] = None, agent_id: Optional[str] = None,
    knowledge_channel: Optional[str] = None, excluded_tool_ids: tuple[str, ...] = (),
    humanization_skill_name: str | None = None,
    media_input: ChatMediaInput | None = None,
    trusted_tool_evidence: tuple[BusinessToolEvidence, ...] = (),
) -> AsyncGenerator[ReplyEvent, None]:
    """Release one checked result to both SSE consumers and persisted IM history."""
    customer_facing = channel_type == 251
    if not customer_facing:
        async for event in _process_internal_ai_stream_to_wukongim(
            project_id=project_id, user_id=user_id, message=message,
            channel_id=channel_id, channel_type=channel_type,
            client_msg_no=client_msg_no, from_uid=from_uid,
            session_id=session_id, system_message=system_message,
            expected_output=expected_output, agent_id=agent_id,
            knowledge_channel=knowledge_channel,
            excluded_tool_ids=excluded_tool_ids,
        ):
            yield event
        return

    full_content = ""
    provider_final = ""
    completed = False
    await forward_ai_event_to_wukongim(
        event_type="agent_execution_started", event_data={"data": {}},
        channel_id=channel_id, channel_type=channel_type,
        client_msg_no=client_msg_no, from_uid=from_uid)
    yield {"event_type": "agent_execution_started", "data": {"data": {}}}
    try:
        media_options: MediaModelOptions = {}
        if media_input is not None:
            prepared = await prepare_chat_media(media_input)
            message = prepared.customer_message
            system_message = (system_message or "") + "\n" + prepared.system_context
            media_options["disable_tools"] = prepared.disable_tools
        if customer_facing:
            system_message = (system_message or "") + "\n" + ASSIST_FACT_GATHERING_PROMPT
        history = (
            await recent_customer_messages(
                channel_id, channel_type, f"{user_id}-vtr",
            )
            if customer_facing else []
        )
        knowledge_evidence: KnowledgeEvidence | None = None
        async for stream_event_type, data in tracked_ai_stream(lambda phase: ai_client.run_supervisor_agent_stream(
            project_id=project_id, agent_id=agent_id, user_id=user_id, message=message,
            session_id=session_id, enable_memory=True, system_message=system_message,
            expected_output=expected_output, knowledge_channel=knowledge_channel,
            excluded_tool_ids=list(excluded_tool_ids),
            cancel_on_disconnect=True,
            reply_phase=phase,
            require_current_knowledge=customer_facing,
            knowledge_context=[
                turn.content[:800] for turn in history
            ][-4:],
            **media_options,
        )):
            event_type = data.get("event_type") or stream_event_type
            if not isinstance(event_type, str):
                raise RuntimeError("Invalid AI event type")
            event_data = data.get("data")
            if event_data is None:
                event_data = {}
            if not isinstance(event_data, dict):
                raise RuntimeError("Invalid AI event payload")
            if event_type in {"workflow_failed", "agent_run_failed", "agent_response_error", "error"}:
                raise RuntimeError("AI factual answer failed")
            if event_type == "agent_content_chunk":
                full_content += _extract_ai_content_chunk(data) or ""
            elif event_type == "agent_tool_call_started":
                full_content = ""
                provider_final = ""
            elif event_type == "agent_response_complete":
                if event_data.get("success") is False:
                    raise RuntimeError("AI factual answer failed")
                final_content = event_data.get("final_content")
                if final_content is not None and not isinstance(final_content, str):
                    raise RuntimeError("Invalid AI final content")
                provider_final = final_content or full_content
                completed = True
                if customer_facing:
                    knowledge_evidence = read_knowledge_evidence(
                        event_data.get("knowledge_evidence"),
                        project_id=project_id, channel=knowledge_channel,
                    )
            elif event_type == "workflow_completed":
                if event_data.get("success") is False:
                    raise RuntimeError("AI workflow failed")
                final_content = event_data.get("final_content")
                if final_content is not None and not isinstance(final_content, str):
                    raise RuntimeError("Invalid AI final content")
                provider_final = final_content or provider_final
                completed = True
                break
        if not completed or not (provider_final or full_content).strip():
            raise RuntimeError("AI stream ended without a complete answer")
        reply = provider_final or full_content
        if customer_facing:
            await ensure_customer_auto_reply(project_id, user_id)
            if knowledge_evidence is None:
                knowledge_evidence = read_knowledge_evidence(
                    None, project_id=project_id, channel=knowledge_channel,
                )
            knowledge_evidence.tool_results.extend(trusted_tool_evidence)
            style = await get_humanization_skill_prompt(
                project_id, humanization_skill_name, message, reply, history) if humanization_skill_name else ""
            reply = await rewrite_assist_draft(
                ai_client, project_id=project_id, agent_id=agent_id,
                customer_message=message, factual_draft=reply,
                humanization_prompt=style, recent_messages=history,
                knowledge_evidence=knowledge_evidence)
            await ensure_customer_auto_reply(project_id, user_id)
        # Only this result crosses the publication boundary. No original chunks
        # or original completion payload are sent to any customer consumer.
        final_data: ReplyEvent = {"success": True, "final_content": reply, "total_chunks": 0}
        await begin_reply_publication()
        await forward_ai_event_to_wukongim(
            event_type="workflow_completed", event_data={"data": final_data},
            channel_id=channel_id, channel_type=channel_type,
            client_msg_no=client_msg_no, from_uid=from_uid)
        yield {"event_type": "agent_content_chunk", "data": {
            "event_type": "agent_content_chunk", "data": {"content_chunk": reply}}}
        for terminal in ("agent_response_complete", "workflow_completed"):
            yield {"event_type": terminal, "data": {
                "event_type": terminal, "data": final_data}}
    except Exception as exc:
        logger.warning("Customer reply was not published: %s", exc)
        error_data: ReplyEvent = {"error_message": str(exc) if isinstance(exc, ReplyStopped)
                      else exc.message if isinstance(exc, MediaInputError)
                      else "回复未通过生成检查，请重试或由人工接待。"}
        await forward_ai_event_to_wukongim(
            event_type="workflow_failed", event_data={"data": error_data},
            channel_id=channel_id, channel_type=channel_type,
            client_msg_no=client_msg_no, from_uid=from_uid)
        yield {"event_type": "workflow_failed", "data": error_data}


async def _process_internal_ai_stream_to_wukongim(
    project_id: str, user_id: str, message: str, channel_id: str,
    channel_type: int, client_msg_no: str, from_uid: str,
    session_id: Optional[str] = None, system_message: Optional[str] = None,
    expected_output: Optional[str] = None, agent_id: Optional[str] = None,
    knowledge_channel: Optional[str] = None,
    excluded_tool_ids: tuple[str, ...] = (),
) -> AsyncIterator[Dict[str, Any]]:
    """Keep staff/internal streams compatible with the event protocol.

    Customer-facing channel 251 uses the checked single-publication path above.
    Staff channels still need the original event-by-event lifecycle for the
    internal console and its tests.
    """
    full_content = ""
    stream_finished = False
    anchor_task = asyncio.create_task(
        forward_ai_event_to_wukongim(
            event_type="agent_execution_started", event_data={"data": {}},
            channel_id=channel_id, channel_type=channel_type,
            client_msg_no=client_msg_no, from_uid=from_uid,
        )
    )
    await asyncio.sleep(0)
    try:
        async for stream_event_type, data in tracked_ai_stream(lambda phase: ai_client.run_supervisor_agent_stream(
            project_id=project_id, agent_id=agent_id, user_id=user_id,
            message=message, session_id=session_id, enable_memory=True,
            system_message=system_message, expected_output=expected_output,
            knowledge_channel=knowledge_channel,
            excluded_tool_ids=list(excluded_tool_ids),
            cancel_on_disconnect=True,
            reply_phase=phase,
        )):
            event_type = data.get("event_type") or stream_event_type
            if not isinstance(event_type, str):
                raise RuntimeError("Invalid AI event type")
            event_to_forward = data
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
                if event_type in {"workflow_completed", "agent_response_complete"} and stream_finished:
                    yield {"event_type": event_type, "data": data}
                    continue
                if event_type in {"workflow_completed", "agent_response_complete"}:
                    await begin_reply_publication()
                    completion_data = data.get("data")
                    if isinstance(completion_data, dict):
                        provider_final = completion_data.get("final_content")
                        if not full_content and isinstance(provider_final, str):
                            full_content = provider_final
                        event_to_forward = {**data, "data": {
                            **completion_data, "final_content": full_content,
                            "total_chunks": 0,
                        }}
                    await anchor_task
                await forward_ai_event_to_wukongim(
                    event_type=event_type, event_data=event_to_forward,
                    channel_id=channel_id, channel_type=channel_type,
                    client_msg_no=client_msg_no, from_uid=from_uid,
                )
                if event_type in {"workflow_completed", "agent_response_complete"}:
                    stream_finished = True
            # Keep the internal SSE terminal payload aligned with what was
            # persisted, especially when an early draft was cleared by a tool.
            yield {"event_type": event_type, "data": event_to_forward}
        if not anchor_task.done():
            await anchor_task
    except Exception as exc:
        logger.error("Error in internal AI stream processing: %s", exc)
        error_data = {"error_message": str(exc)}
        await anchor_task
        await forward_ai_event_to_wukongim(
            event_type="workflow_failed", event_data={"data": error_data},
            channel_id=channel_id, channel_type=channel_type,
            client_msg_no=client_msg_no, from_uid=from_uid,
        )
        yield {"event_type": "workflow_failed", "data": error_data}
    finally:
        if not anchor_task.done():
            anchor_task.cancel()
        await asyncio.gather(anchor_task, return_exceptions=True)


async def handle_ai_response_non_stream(
    project_id: str, visitor_id: str, message: str, channel_id: str,
    channel_type: int, client_msg_no: str, from_uid: str,
    session_id: Optional[str] = None, system_message: Optional[str] = None,
    expected_output: Optional[str] = None, agent_id: Optional[str] = None,
    knowledge_channel: Optional[str] = None, excluded_tool_ids: tuple[str, ...] = (),
    humanization_skill_name: str | None = None,
    media_input: ChatMediaInput | None = None,
) -> Dict[str, Any]:
    """Non-stream clients consume exactly the same checked result."""
    content = ""
    last_data = {}
    failure: str | None = None
    async for event in process_ai_stream_to_wukongim(
        project_id=project_id, user_id=visitor_id, message=message,
        channel_id=channel_id, channel_type=channel_type, client_msg_no=client_msg_no,
        from_uid=from_uid, session_id=session_id, system_message=system_message,
        expected_output=expected_output, agent_id=agent_id, knowledge_channel=knowledge_channel,
        excluded_tool_ids=excluded_tool_ids, humanization_skill_name=humanization_skill_name,
        media_input=media_input,
    ):
        last_data = event["data"]
        if event["event_type"] == "human_handoff":
            return {"success": True, "handoff": True, "content": "", "data": last_data}
        if event["event_type"] == "workflow_failed":
            failure = last_data["error_message"]
        if event["event_type"] in {"agent_response_complete", "workflow_completed"}:
            terminal_data = last_data.get("data") if isinstance(last_data, dict) else None
            if isinstance(terminal_data, dict) and isinstance(terminal_data.get("final_content"), str):
                content = terminal_data["final_content"]
        if event["event_type"] == "agent_content_chunk":
            content += _extract_ai_content_chunk(last_data) or ""
    if failure is not None:
        return {"success": False, "error": failure}
    return {"success": True, "content": content, "data": last_data}


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
    humanization_skill_name: str | None = None,
    media_input: ChatMediaInput | None = None,
) -> None:
    """Run AI interaction in the background.

    Args:
        started_event: Optional asyncio.Event that will be set when agent execution starts.
    """
    failed = False
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
        humanization_skill_name=humanization_skill_name,
        media_input=media_input,
    ):
        if event_payload.get("event_type") == "workflow_failed":
            failed = True
        # Signal that AI processing has started
        if started_event and not started_event.is_set():
            event_type = event_payload.get("event_type")
            if event_type == "agent_execution_started":
                started_event.set()
    if failed:
        raise RuntimeError("Customer reply was not published")


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
    humanization_skill_name: str | None = None,
    media_input: ChatMediaInput | None = None,
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
            humanization_skill_name=humanization_skill_name,
            media_input=media_input,
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
    require_delivery: bool = False,
) -> Optional[str]:
    """Send a copy of the user's message to WuKongIM (best-effort)."""
    if not content:
        return None
    if require_delivery and not wukongim_client.enabled:
        raise RuntimeError("WuKongIM delivery is unavailable for media")
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
        # 1=TEXT, 2=IMAGE, 3=FILE, 4=VOICE
        payload: Dict[str, Any] = {
            "type": int(msg_type or MessageType.TEXT),
            "content": content,
        }
        if msg_type == MessageType.IMAGE:
            payload["url"] = content
        elif msg_type == MessageType.VOICE:
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
        if require_delivery:
            raise
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

    from app.services.company_entitlements import require_human_service
    require_human_service(db, platform.project_id, visitor.id if visitor else None)
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
