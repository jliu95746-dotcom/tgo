"""Build unread assist reminders within the authenticated platform scope."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.schemas.wecom_native import NativeAssistNotification

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.platform import Platform


def pending_assist_notifications(
    db: "Session", platform: "Platform"
) -> list[NativeAssistNotification]:
    from app.models.visitor import Visitor
    from app.services.chat_service import resolve_service_mode

    config = platform.config or {}
    if not config.get("app_notification_enabled"):
        return []
    try:
        enabled = datetime.fromisoformat(
            str(config["app_notification_enabled_at"])
        )
        since = max(
            enabled.astimezone(UTC), datetime.now(UTC) - timedelta(days=1)
        ).replace(tzinfo=None)
    except (KeyError, ValueError, TypeError):
        return (
            []
        )  # Never replay old conversations when enablement is incomplete.
    rows = (
        db.query(Visitor)
        .filter(
            Visitor.project_id == platform.project_id,
            Visitor.platform_id == platform.id,
            Visitor.deleted_at.is_(None),
            Visitor.service_status != "closed",
            Visitor.is_last_message_from_visitor.is_(True),
            Visitor.last_message_at >= since,
        )
        .order_by(Visitor.last_message_at.desc())
        .limit(100)
        .all()
    )
    return [
        NativeAssistNotification(
            visitor_id=row.id,
            event_id=row.last_client_msg_no
            or f"{row.last_message_seq}:{row.last_message_at.isoformat()}",
        )
        for row in rows
        if resolve_service_mode(platform, row) == "assist"
    ]
