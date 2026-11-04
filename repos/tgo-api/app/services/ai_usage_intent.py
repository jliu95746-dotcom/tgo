"""Route commercial visitor messages inside their existing reply quota permit."""

from uuid import UUID
import logging
import time

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import Platform, Project, Visitor
from app.models.ai_interaction_run import AIInteractionRun
from app.services.message_intent_orchestrator import (
    MessageIntentOrchestrator,
    MessageIntentRoutingOutcome,
)


async def route_reply_intent(
    project_id: str, client_msg_no: str, message: str, *, defer_business_tools: bool = False,
) -> MessageIntentRoutingOutcome | None:
    from app.services.customer_turn_context import current_execution
    if (not settings.SAAS_ENABLED or not settings.SAAS_BILLING_ENABLED) and current_execution.get() is None:
        return None
    with SessionLocal() as db:
        run = db.scalar(
            select(AIInteractionRun).where(
                AIInteractionRun.project_id == UUID(project_id),
                AIInteractionRun.response_client_msg_no == client_msg_no,
            )
        )
        if run is None:
            return None
        project = db.get(Project, run.project_id)
        visitor = db.get(Visitor, run.visitor_id)
        platform = db.get(Platform, run.platform_id)
        if project is None or visitor is None or platform is None:
            raise ValueError("Reply routing context no longer exists")
        from app.services.humanization_service import recent_customer_messages
        history = await recent_customer_messages(run.channel_id, run.channel_type, f"{visitor.id}-vtr")
        started = time.monotonic()
        execution = current_execution.get()
        result = await MessageIntentOrchestrator(db).analyze_text_message(
            project=project,
            platform=platform,
            visitor=visitor,
            source_message_id=run.source_message_id,
            user_text=message,
            reuse_existing=True,
            defer_business_tools=defer_business_tools,
            conversation_context=[f"{turn.role}: {turn.content}" for turn in history],
            media_input=execution.media_input if execution else None,
        )
        context = current_execution.get()
        if context:
            logging.getLogger(__name__).info("Customer turn phase turn=%s version=%s phase=intent seconds=%.3f route=%s",
                context.turn_id, context.version, time.monotonic() - started, result.routing_target)
            context.generation_started = time.monotonic()
        return result
