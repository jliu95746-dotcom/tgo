from __future__ import annotations
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Platform
from app.domain.entities import NormalizedMessage, ChatCompletionRequest
from app.domain.ports import TgoApiClient, SSEManager, PlatformAdapter
from app.domain.services.adapters import SimpleStdoutAdapter, EmailAdapter, WeComAdapter, WeComBotAdapter, FeishuBotAdapter, DingTalkBotAdapter, TelegramAdapter, SlackAdapter


def _expected_output_for(ptype: str) -> str | None:
    p = (ptype or "").lower()
    if p == "wecom":
        return "text"
    if p == "wecom_bot":
        return "text"  # WeCom Bot supports text and markdown, default to text
    if p == "feishu_bot":
        return "text"  # Feishu Bot uses text for reply
    if p == "dingtalk_bot":
        return "text"  # DingTalk Bot uses text for reply
    if p == "telegram":
        return "text"  # Telegram supports Markdown but default to text
    if p == "email":
        return "markdown"
    return None


def _default_system_message_for(ptype: str) -> str | None:
    p = (ptype or "").lower()
    if p == "email":
        return (
            "You are responding to an email. Please format your response as a professional "
            "email reply with appropriate greeting, body, and closing."
        )
    if p == "wecom":
        return None
    if p == "wecom_bot":
        return None
    if p == "feishu_bot":
        return None
    if p == "dingtalk_bot":
        return None
    if p == "telegram":
        return None
    return None



async def select_adapter_for_target(msg: NormalizedMessage, platform: Platform) -> PlatformAdapter:
    """Choose platform adapter based on platform.type and per-platform config.

    For type="email", construct EmailAdapter using SMTP settings from Platform.config.
    For type="wecom", construct WeComAdapter using per-platform config and message sender as target.
    Otherwise, default to SimpleStdoutAdapter.
    """
    ptype = (platform.type or "").lower()
    if ptype == "email":
        cfg = platform.config or {}
        smtp_host = cfg.get("smtp_host")
        smtp_port = int(cfg.get("smtp_port", 587))
        smtp_username = cfg.get("smtp_username")
        smtp_password = cfg.get("smtp_password")
        smtp_use_tls = bool(cfg.get("smtp_use_tls", False))
        from_addr = smtp_username
        # Determine addressing and subject from message extras
        to_addr = (msg.extra or {}).get("email_to") or msg.from_uid
        subject = (msg.extra or {}).get("subject") or ""
        if not (smtp_host and smtp_username and smtp_password and from_addr and to_addr):
            # Fallback to stdout if config incomplete
            return SimpleStdoutAdapter()
        return EmailAdapter(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_username=smtp_username,
            smtp_password=smtp_password,
            smtp_use_tls=smtp_use_tls,
            to_addr=to_addr,
            from_addr=from_addr,
            subject=subject,
        )
    if ptype == "wecom":
        cfg = platform.config or {}
        corp_id = cfg.get("corp_id")
        agent_id = cfg.get("agent_id")
        app_secret = cfg.get("app_secret")
        to_user = msg.from_uid
        wc = ((msg.extra or {}).get("wecom") or {})
        is_from_colleague = bool(wc.get("is_from_colleague", True))
        open_kfid = wc.get("open_kfid")
        external_userid = wc.get("external_userid") or msg.from_uid
        if not (corp_id and app_secret and to_user):
            raise RuntimeError("WeCom adapter requires corp_id, app_secret, and recipient")
        if is_from_colleague and not agent_id:
            raise RuntimeError("WeCom colleague reply requires agent_id")
        if not is_from_colleague and not (open_kfid and external_userid):
            raise RuntimeError("WeCom KF reply requires open_kfid and external_userid")
        return WeComAdapter(
            corp_id=corp_id,
            agent_id=str(agent_id) if agent_id is not None else None,
            app_secret=app_secret,
            to_user=to_user,
            is_from_colleague=is_from_colleague,
            open_kfid=open_kfid,
            external_userid=external_userid,
            source_message_id=(msg.extra or {}).get("message_id"),
        )
    if ptype == "wecom_bot":
        # Get wecom context which contains response_url from the incoming message
        wc = ((msg.extra or {}).get("wecom") or {})
        # response_url is required for replying to the message
        response_url = wc.get("response_url") or ""
        if not response_url:
            return SimpleStdoutAdapter()
        return WeComBotAdapter(response_url=response_url)
    if ptype == "feishu_bot":
        cfg = platform.config or {}
        # Get feishu context which contains message_id for reply
        fc = ((msg.extra or {}).get("feishu") or {})
        app_id = fc.get("app_id") or cfg.get("app_id") or ""
        app_secret = fc.get("app_secret") or cfg.get("app_secret") or ""
        message_id = fc.get("message_id") or ""
        if not (app_id and app_secret and message_id):
            return SimpleStdoutAdapter()
        return FeishuBotAdapter(
            app_id=app_id,
            app_secret=app_secret,
            message_id=message_id,
        )
    if ptype == "dingtalk_bot":
        # Get dingtalk context which contains session_webhook for reply
        dc = ((msg.extra or {}).get("dingtalk") or {})
        session_webhook = dc.get("session_webhook") or ""
        if not session_webhook:
            return SimpleStdoutAdapter()
        return DingTalkBotAdapter(session_webhook=session_webhook)
    if ptype == "telegram":
        cfg = platform.config or {}
        # Get telegram context which contains chat_id for reply
        tc = ((msg.extra or {}).get("telegram") or {})
        bot_token = tc.get("bot_token") or cfg.get("bot_token") or ""
        chat_id = tc.get("chat_id") or msg.from_uid or ""
        if not (bot_token and chat_id):
            return SimpleStdoutAdapter()
        return TelegramAdapter(bot_token=bot_token, chat_id=chat_id)
    if ptype == "slack":
        cfg = platform.config or {}
        # Get slack context which contains channel for reply
        sc = ((msg.extra or {}).get("slack") or {})
        bot_token = sc.get("bot_token") or cfg.get("bot_token") or ""
        channel = sc.get("channel") or ""
        thread_ts = sc.get("thread_ts")  # Optional: reply in thread
        if not (bot_token and channel):
            return SimpleStdoutAdapter()
        return SlackAdapter(bot_token=bot_token, channel=channel, thread_ts=thread_ts)
    return SimpleStdoutAdapter()


def _normalize_message_type(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        stripped = value.strip().lower()
        if stripped.isdigit():
            return int(stripped)
        return {
            "text": 1,
            "image": 2,
            "file": 3,
            "voice": 4,
            "video": 5,
        }.get(stripped, 1)
    return 1


def _extract_text_value(value: object, depth: int = 0) -> str | None:
    if depth > 4 or not isinstance(value, dict):
        return None
    for key in ("content_chunk", "content", "text"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    nested = value.get("data")
    return _extract_text_value(nested, depth + 1)


def _extract_named_value(
    value: object,
    name: str,
    depth: int = 0,
) -> object | None:
    if depth > 4 or not isinstance(value, dict):
        return None
    if name in value:
        return value[name]
    return _extract_named_value(value.get("data"), name, depth + 1)


def _upstream_extra(extra: dict | None) -> dict[str, object] | None:
    if not extra:
        return None
    message_id = extra.get("message_id")
    if isinstance(message_id, str) and message_id:
        return {"message_id": message_id}
    return None


async def process_message(
    msg: NormalizedMessage,
    db: AsyncSession,
    tgo_api_client: TgoApiClient,
    sse_manager: SSEManager,
) -> str | None:
    """End-to-end orchestration using DB platform config + tgo-api SSE + adapter output.

    Returns the final reply text if available (for non-streaming adapters), otherwise None.
    """
    if not getattr(msg, "platform_api_key", None):
        raise RuntimeError("platform_api_key missing on NormalizedMessage")
    platform = await db.scalar(select(Platform).where(Platform.id == msg.platform_id))
    if platform is None:
        raise RuntimeError(f"Platform {msg.platform_id} not found")

    ptype = ((platform.type or msg.platform_type) or "").lower()
    expected_output = _expected_output_for(ptype)
    default_system_message = _default_system_message_for(ptype)
    system_message = (msg.extra or {}).get("system_message") or default_system_message
    request = ChatCompletionRequest(
        api_key=msg.platform_api_key,
        message=msg.content,
        from_uid=msg.from_uid or "",
        msg_type=_normalize_message_type((msg.extra or {}).get("msg_type")),
        system_message=system_message,
        expected_output=expected_output,
        extra=_upstream_extra(msg.extra),
        timeout_seconds=settings.request_timeout_seconds,
    )
    frames = tgo_api_client.chat_completion(request)
    events = sse_manager.stream_events(frames)
    adapter = await select_adapter_for_target(msg, platform=platform)

    if adapter.supports_stream:
        async for event in events:
            await adapter.send_incremental(event)
        return None

    chunk_events = {"agent_content_chunk"}
    success_events = {"workflow_completed"}
    failure_events = {
        "error",
        "disconnected",
        "workflow_failed",
        "agent_run_failed",
        "agent_response_error",
        "team_run_failed",
        "team_member_failed",
        "stream.error",
    }
    no_reply_events = {"ai_disabled", "assist_mode", "queued"}
    chunks: list[str] = []
    final_content: str | None = None
    completed = False

    async for event in events:
        payload = event.payload or {}
        event_type = str(payload.get("event_type") or event.event or "")
        if event_type in no_reply_events:
            logging.info(
                "[DISPATCH] No automatic reply for event=%s platform_id=%s",
                event_type,
                msg.platform_id,
            )
            return None
        if event_type in failure_events or event.event in failure_events:
            raise RuntimeError(f"AI stream failed with event {event_type or event.event}")
        if event_type == "agent_response_complete":
            success = _extract_named_value(payload.get("data"), "success")
            if success is False:
                error_detail = _extract_named_value(payload.get("data"), "error")
                raise RuntimeError(
                    f"AI agent response failed: {error_detail or 'unknown error'}"
                )
            final_value = _extract_named_value(
                payload.get("data"),
                "final_content",
            )
            if isinstance(final_value, str) and final_value:
                final_content = final_value
        if event_type in chunk_events:
            text = _extract_text_value(payload.get("data"))
            if text:
                chunks.append(text)
        elif event.event == "message" and not payload.get("event_type"):
            text = _extract_text_value(payload)
            if text:
                chunks.append(text)
        if event_type in success_events:
            success = _extract_named_value(payload.get("data"), "success")
            if success is False:
                raise RuntimeError("AI workflow completed with success=false")
            completed = True
            break

    if not completed:
        raise RuntimeError("AI stream ended without a success event")
    reply_text = "".join(chunks) or final_content or ""
    if not reply_text:
        raise RuntimeError("AI stream completed without reply text")
    final = {"text": reply_text}
    await adapter.send_final(final)
    return reply_text

