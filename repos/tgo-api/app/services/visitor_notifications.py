"""Visitor update notification helpers."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import Visitor
from app.services.wukongim_client import wukongim_client
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import build_visitor_channel_id

logger = logging.getLogger("services.visitor_notifications")


async def notify_visitor_profile_updated(
    db: Session, visitor: Visitor
) -> None:
    """
    Notify all staff associated with the visitor's customer-service channel that the profile was updated.
    """
    channel_id = build_visitor_channel_id(visitor.id)

    try:
        await wukongim_client.send_visitor_profile_updated(
            visitor_id=str(visitor.id),
            channel_id=channel_id,
            channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
        )
    except Exception as e:
        logger.error(
            "Failed to dispatch visitor profile update notification",
            exc_info=e,
            extra={"visitor_id": str(visitor.id)},
        )
