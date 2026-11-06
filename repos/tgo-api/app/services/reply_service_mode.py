"""Recheck the authoritative customer mode before publishing an AI answer."""

import asyncio
from uuid import UUID

from fastapi import HTTPException

from app.core.database import SessionLocal
from app.models import Platform, Visitor
from app.services.ai_reply_control import ReplyStopped
from app.services.timed_ai_fallback import allows_timed_reply
from app.services.wechat_local_bridge import require_scoped_target
from app.utils.wechat_local import is_scoped_identity


def _require_auto_mode(project_id: str, visitor_id: str) -> tuple[Platform, str] | None:
    from app.services.chat_service import is_ai_disabled

    with SessionLocal() as db:
        timed_reply = allows_timed_reply(db, project_id, visitor_id)
        visitor = db.get(Visitor, UUID(visitor_id))
        if (
            visitor is None
            or visitor.project_id != UUID(project_id)
            or visitor.deleted_at is not None
            or visitor.access_retired_at is not None
        ):
            raise ReplyStopped("客户会话已失效，AI 自动回复已停止。")
        platform = db.get(Platform, visitor.platform_id)
        if (
            platform is None
            or platform.project_id != visitor.project_id
            or platform.deleted_at is not None
            or not platform.is_active
            or (
                is_ai_disabled(platform, visitor)
                and not timed_reply
            )
        ):
            raise ReplyStopped("会话已切换至人工接待，AI 自动回复已停止。")
        identity = getattr(visitor, "platform_open_id", None)
        if getattr(platform, "type", None) == "wechat_local" and isinstance(identity, str) and is_scoped_identity(identity):
            if not platform.is_active:
                raise ReplyStopped("个人微信渠道已停用，AI 自动回复已停止。")
            return platform, identity
        return None


async def ensure_customer_auto_reply(
    project_id: str,
    visitor_id: str,
) -> None:
    """Use a fresh DB session; a model request may have changed the mode."""
    scoped = await asyncio.to_thread(_require_auto_mode, project_id, visitor_id)
    if scoped is not None:
        try:
            await require_scoped_target(*scoped)
        except HTTPException as exc:
            raise ReplyStopped("个人微信会话授权已失效，AI 自动回复已停止。") from exc
