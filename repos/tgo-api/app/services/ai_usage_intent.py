"""Route commercial visitor messages inside their existing reply quota permit."""

from uuid import UUID

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
    project_id: str, client_msg_no: str, message: str
) -> MessageIntentRoutingOutcome | None:
    if not settings.SAAS_ENABLED or not settings.SAAS_BILLING_ENABLED:
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
        return await MessageIntentOrchestrator(db).analyze_text_message(
            project=project,
            platform=platform,
            visitor=visitor,
            source_message_id=run.source_message_id,
            user_text=message,
            reuse_existing=True,
        )
