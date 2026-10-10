"""Recheck the authoritative customer mode before publishing an AI answer."""

import asyncio
from uuid import UUID

from app.core.database import SessionLocal
from app.models import Platform, Visitor
from app.services.ai_reply_control import ReplyStopped


def _require_auto_mode(project_id: str, visitor_id: str) -> None:
    from app.services.chat_service import is_ai_disabled

    with SessionLocal() as db:
        visitor = db.get(Visitor, UUID(visitor_id))
        if (
            visitor is None
            or visitor.project_id != UUID(project_id)
            or visitor.deleted_at is not None
        ):
            raise ReplyStopped("客户会话已失效，AI 自动回复已停止。")
        platform = db.get(Platform, visitor.platform_id)
        if (
            platform is None
            or platform.project_id != visitor.project_id
            or platform.deleted_at is not None
            or is_ai_disabled(platform, visitor)
        ):
            raise ReplyStopped("会话已切换至人工接待，AI 自动回复已停止。")


async def ensure_customer_auto_reply(
    project_id: str,
    visitor_id: str,
) -> None:
    """Use a fresh DB session; a model request may have changed the mode."""
    await asyncio.to_thread(_require_auto_mode, project_id, visitor_id)
