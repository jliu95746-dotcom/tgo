"""Shared private Workflow endpoint authentication."""

import hmac

from fastapi import HTTPException

from app.config import settings


def require_internal_key(key: str | None) -> None:
    configured = settings.SECRET_KEY
    if configured is None or not configured.get_secret_value():
        raise HTTPException(503, "工作流内部审批核验未配置")
    if not key or not hmac.compare_digest(key, configured.get_secret_value()):
        raise HTTPException(403, "工作流内部审批身份无法确认")
