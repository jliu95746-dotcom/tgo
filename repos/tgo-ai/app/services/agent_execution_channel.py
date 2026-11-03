"""Only an authenticated API hop can select a managed employee's channel."""

import hmac
from uuid import UUID

from fastapi import HTTPException, Request

from app.config import settings
from app.schemas.knowledge import KnowledgeChannel


def trusted_execution_channel(request: Request, project: UUID) -> KnowledgeChannel | None:
    key = request.headers.get("X-Internal-API-Key", "")
    raw = request.headers.get("X-Execution-Channel", "")
    if not key and not raw:
        return None
    if not key or not hmac.compare_digest(key, settings.secret_key):
        raise HTTPException(403, "员工执行身份无法确认")
    try:
        if UUID(request.headers.get("X-Project-Id", "")) != project:
            raise ValueError("Project mismatch")
        return KnowledgeChannel(raw)
    except ValueError:
        raise HTTPException(403, "员工执行渠道或企业范围无法确认") from None
