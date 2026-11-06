"""Run configured assist-mode timeouts against one durable customer source."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import logging
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import Platform, Visitor
from app.models.ai_interaction_run import AIInteractionRun
from app.services.ai_interaction_run_service import (
    AIInteractionIdentity,
    build_request_fingerprint,
    claim_ai_interaction,
    mark_ai_interaction_finished,
)
from app.services.ai_reply_control import ReplyStopped
from app.services.chat_service import (
    forward_ai_event_to_wukongim,
    handle_ai_response_non_stream,
)
from app.services.fallback_reply_attempt import (
    FallbackReplyAttempt,
    attempt_scope,
)
from app.services.employee_style import resolve_employee_style
from app.services.knowledge_channel import resolve_platform_knowledge_channel
from app.services.timed_ai_fallback import (
    MAX_AI_FALLBACK_RETRIES,
    FallbackSource,
    capture_source,
    fallback_scope,
    still_current,
)
from app.services.visitor_chat_access import guarded_chat_call
from app.services.wukongim_client import wukongim_client
from app.utils.const import (
    AI_FALLBACK_RETRYABLE_FAILURE,
    CHANNEL_TYPE_CUSTOMER_SERVICE,
)
from app.utils.encoding import build_visitor_channel_id, get_session_id

logger = logging.getLogger(__name__)
_auto_fallback_task: asyncio.Task[None] | None = None


async def start_auto_fallback_to_ai_task(interval_seconds: int = 60) -> None:
    global _auto_fallback_task
    if _auto_fallback_task is not None:
        return

    async def loop() -> None:
        while True:
            try:
                await check_and_fallback_to_ai()
            except Exception as error:
                logger.error(
                    "AI fallback loop failed: %s", type(error).__name__
                )
            await asyncio.sleep(interval_seconds)

    _auto_fallback_task = asyncio.create_task(loop())
    logger.info("Started auto fallback to AI periodic task")


async def stop_auto_fallback_to_ai_task() -> None:
    global _auto_fallback_task
    if _auto_fallback_task is not None:
        _auto_fallback_task.cancel()
        try:
            await _auto_fallback_task
        except asyncio.CancelledError:
            pass
        _auto_fallback_task = None
        logger.info("Stopped auto fallback to AI periodic task")


def update_source(
    db: Session, source: FallbackSource, *, response: str | None = None,
    exhausted: bool = False,
) -> bool:
    if not still_current(db, source):
        return False
    statement = update(Visitor).where(
        Visitor.id == source.visitor_id,
        Visitor.project_id == source.project_id,
        Visitor.platform_id == source.platform_id,
        Visitor.last_client_msg_no == source.source_message_id,
        Visitor.last_message_at == source.source_at,
        Visitor.is_last_message_from_visitor.is_(True),
        Visitor.service_status == "active",
        or_(Visitor.ai_disabled.is_(None), Visitor.ai_disabled.is_(False)),
        Visitor.service_mode.is_(None),
        Visitor.access_retired_at.is_(None),
        Visitor.deleted_at.is_(None),
    )
    if response is not None:
        statement = statement.values(
            is_last_message_from_ai=True, is_last_message_from_visitor=False,
            last_client_msg_no=response, ai_fallback_retry_count=0,
        )
    else:
        statement = statement.values(ai_fallback_retry_count=(
            MAX_AI_FALLBACK_RETRIES if exhausted
            else Visitor.ai_fallback_retry_count + 1
        ))
    result = db.execute(statement.returning(Visitor.id).execution_options(
        synchronize_session=False
    ))
    changed = result.scalar_one_or_none() is not None
    db.commit()
    return changed


async def finish_failed_attempt(
    db: Session, source: FallbackSource, attempt: FallbackReplyAttempt
) -> bool:
    retry_allowed = attempt.retry_allowed
    db.expire_all()
    visitor = db.get(Visitor, source.visitor_id)
    last_attempt = (
        visitor is None
        or visitor.ai_fallback_retry_count >= MAX_AI_FALLBACK_RETRIES - 1
    )
    if (not retry_allowed or last_attempt) and attempt.can_notify_failure:
        attempt.publication_started = True
        with fallback_scope(attempt.source), attempt_scope(attempt):
            await guarded_chat_call(lambda: forward_ai_event_to_wukongim(
                event_type="workflow_failed", event_data={"data": {}},
                channel_id=build_visitor_channel_id(source.visitor_id),
                channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
                client_msg_no=attempt.source.response_client_msg_no or "",
                from_uid=f"{source.staff_id}-staff",
            ))
    changed = update_source(db, source, exhausted=not retry_allowed)
    return retry_allowed and not last_attempt and changed


async def fallback_one(
    db: Session, platform: Platform, visitor: Visitor
) -> None:
    source = capture_source(db, platform, visitor)
    if source is None:
        return
    channel_id = build_visitor_channel_id(source.visitor_id)
    channel_type = CHANNEL_TYPE_CUSTOMER_SERVICE
    with fallback_scope(source):
        message = await guarded_chat_call(
            lambda: wukongim_client.get_message_by_client_msg_no(
                channel_id=channel_id, channel_type=channel_type,
                client_msg_no=source.source_message_id, raise_on_error=True,
            )
        )
        content = message.payload.get("content") if message else None
        if (
            message is None
            or message.client_msg_no != source.source_message_id
            or message.channel_id != channel_id
            or message.channel_type != channel_type
            or message.from_uid != f"{source.visitor_id}-vtr"
            or message.payload.get("type") != 1
            or not isinstance(content, str) or not content.strip()
        ):
            update_source(db, source, exhausted=True)
            return
        extra = message.payload.get("extra")
        original_id = (
            extra.get("message_id") if isinstance(extra, dict) else None
        )
        if not (
            isinstance(original_id, str)
            and original_id.strip() and len(original_id) <= 255
        ):
            if platform.type != "website":
                update_source(db, source, exhausted=True)
                return
            original_id = source.source_message_id
        try:
            style = await guarded_chat_call(lambda: resolve_employee_style(
                str(source.project_id),
                str(source.agent_id) if source.agent_id else None,
                visitor.humanization_skill_name,
                visitor.humanization_skill_enabled,
                require_active=True,
            ))
        except ReplyStopped:
            raise
        except Exception as error:
            update_source(db, source)
            logger.warning(
                "Fallback employee unavailable: %s", type(error).__name__
            )
            return

        identity = AIInteractionIdentity(
            project_id=source.project_id, platform_id=source.platform_id,
            visitor_id=source.visitor_id, channel_id=channel_id,
            channel_type=channel_type,
            source_message_id=original_id,
        )
        previous = db.scalar(select(AIInteractionRun).where(
            AIInteractionRun.project_id == identity.project_id,
            AIInteractionRun.platform_id == identity.platform_id,
            AIInteractionRun.channel_id == identity.channel_id,
            AIInteractionRun.channel_type == identity.channel_type,
            AIInteractionRun.source_message_id == identity.source_message_id,
        ))
        if (
            previous is not None and previous.status == "failed"
            and previous.error_message != AI_FALLBACK_RETRYABLE_FAILURE
        ):
            update_source(db, source, exhausted=True)
            return
        claim = claim_ai_interaction(
            db, identity=identity,
            request_fingerprint=build_request_fingerprint(
                visitor_id=source.visitor_id, message=content, message_type=1,
            ),
            response_client_msg_no=f"ai_fallback_{uuid4().hex}",
        )
        if claim.is_duplicate:
            return
        response_id, run_id = claim.run.response_client_msg_no, claim.run.id
        error_code: str | None = "ai_processing_failed"
        outcome_saved = False
        retryable_fallback = False
        attempt = FallbackReplyAttempt(replace(
            source, response_client_msg_no=response_id
        ))
        try:
            with fallback_scope(attempt.source), attempt_scope(attempt):
                result = await handle_ai_response_non_stream(
                    project_id=str(source.project_id),
                    visitor_id=str(source.visitor_id), message=content,
                    channel_id=channel_id, channel_type=channel_type,
                    client_msg_no=response_id,
                    from_uid=f"{source.staff_id}-staff",
                    session_id=get_session_id(
                        f"{source.staff_id}-staff", channel_id, channel_type
                    ),
                    agent_id=style.agent_id,
                    humanization_skill_name=(
                        style.skill_name if style.enabled else None
                    ),
                    knowledge_channel=resolve_platform_knowledge_channel(
                        platform.type
                    ).value,
                    interaction_run_id=run_id,
                )
            result_content = result.get("content")
            if result.get("handoff") is True:
                error_code = None
                update_source(db, source, exhausted=True)
            elif (
                result.get("success") is True
                and isinstance(result_content, str) and result_content.strip()
            ):
                error_code = None
                update_source(db, source, response=response_id)
            else:
                retryable_fallback = await finish_failed_attempt(
                    db, source, attempt
                )
            outcome_saved = True
        except (ReplyStopped, asyncio.CancelledError):
            update_source(db, source, exhausted=True)
            outcome_saved = True
            raise
        except Exception as error:
            retryable_fallback = await finish_failed_attempt(
                db, source, attempt
            )
            outcome_saved = True
            logger.warning("AI fallback interrupted: %s", type(error).__name__)
        finally:
            if outcome_saved:
                if retryable_fallback:
                    mark_ai_interaction_finished(
                        run_id, response_client_msg_no=response_id,
                        error_message=error_code, retryable_fallback=True,
                    )
                else:
                    mark_ai_interaction_finished(
                        run_id, response_client_msg_no=response_id,
                        error_message=error_code,
                    )


async def check_and_fallback_to_ai() -> None:
    with SessionLocal() as db:
        try:
            platforms = db.query(Platform).filter(
                Platform.type != "wechat_local", Platform.ai_mode == "assist",
                Platform.fallback_to_ai_timeout > 0,
                Platform.is_active.is_(True), Platform.deleted_at.is_(None),
            ).all()
            for platform in platforms:
                timeout = platform.fallback_to_ai_timeout
                if timeout is None or timeout <= 0:
                    continue
                cutoff = (
                    datetime.now(timezone.utc) - timedelta(seconds=timeout)
                ).replace(tzinfo=None)
                visitors = db.query(Visitor).filter(
                    Visitor.project_id == platform.project_id,
                    Visitor.platform_id == platform.id,
                    Visitor.service_status == "active",
                    Visitor.is_last_message_from_visitor.is_(True),
                    Visitor.last_message_at < cutoff,
                    Visitor.last_client_msg_no.is_not(None),
                    Visitor.service_mode.is_(None),
                    Visitor.access_retired_at.is_(None),
                    Visitor.ai_fallback_retry_count < MAX_AI_FALLBACK_RETRIES,
                    Visitor.deleted_at.is_(None),
                    or_(Visitor.ai_disabled.is_(None),
                        Visitor.ai_disabled.is_(False)),
                ).all()
                for visitor in visitors:
                    try:
                        await fallback_one(db, platform, visitor)
                    except ReplyStopped:
                        db.rollback()
                    except Exception as error:
                        db.rollback()
                        logger.warning(
                            "AI fallback deferred: %s", type(error).__name__
                        )
        except Exception as error:
            db.rollback()
            logger.error("AI fallback scan failed: %s", type(error).__name__)
