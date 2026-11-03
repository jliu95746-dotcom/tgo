"""Retire owned access before trying to stop its upstream work."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.models.platform import Platform
from app.models.visitor import Visitor
from app.models.ai_interaction_run import AIInteractionRun
from app.schemas.visitor_access import (
    VisitorAccessEndRequest, VisitorAccessEndResponse,
)
from app.services.ai_reply_control import run_registry
from app.services.visitor_access import current_visitor, denied, end_access
from app.services.wukongim_client import wukongim_client
from app.utils.encoding import build_visitor_channel_id

CLEANUP_PAGE_SIZE = 100
CLEANUP_SECONDS = 3.0
IM_DISCONNECT_LEASE_SECONDS = 30


def _release_im_lease(
    db: Session, project_id: UUID, visitor_id: UUID, token: UUID,
) -> None:
    """Release only the IM claim owned by this request."""
    try:
        db.execute(
            update(Visitor).where(
                Visitor.project_id == project_id,
                Visitor.id == visitor_id,
                Visitor.im_disconnect_lease_token == token,
            ).values(
                im_disconnect_lease_until=None,
                im_disconnect_lease_token=None,
            )
        )
        db.commit()
    except Exception:
        db.rollback()


async def end_owned_access(
    db: Session, request: VisitorAccessEndRequest,
) -> VisitorAccessEndResponse:
    visitor = db.scalar(
        select(Visitor).join(
            Platform, Visitor.platform_id == Platform.id,
        ).where(
            Visitor.id == request.visitor_id,
            Platform.api_key == request.platform_api_key,
            Platform.project_id == Visitor.project_id,
            Platform.type == "website",
        )
    )
    if visitor is None:
        raise denied()
    # Use the registration lock for a first end and a repeated receipt.
    visitor = current_visitor(db, visitor, allow_retired=True)
    end_access(db, visitor, request, end_token=request.visitor_end_token)
    # Invalid credentials return above, before any external side effect.
    return await cleanup_retired_access(db, visitor)


async def cleanup_retired_access(
    db: Session, visitor: Visitor,
) -> VisitorAccessEndResponse:
    """Retry unfinished cleanup for the original retired visitor."""
    if visitor.access_retired_at is None:
        raise ValueError("Visitor access is not retired")
    project_id = str(visitor.project_id)
    project_uuid, visitor_uuid = visitor.project_id, visitor.id
    uid = build_visitor_channel_id(visitor.id)
    confirmed = True
    last_run_id = None
    try:
        async with asyncio.timeout(CLEANUP_SECONDS):
            while True:
                query = select(
                    AIInteractionRun.id,
                    AIInteractionRun.response_client_msg_no,
                    AIInteractionRun.stop_confirmed_at,
                ).where(
                    AIInteractionRun.project_id == project_uuid,
                    AIInteractionRun.visitor_id == visitor_uuid,
                    (AIInteractionRun.status == "running") |
                    (AIInteractionRun.error_message == "visitor_access_ended"),
                )
                if last_run_id is not None:
                    query = query.where(AIInteractionRun.id > last_run_id)
                batch = db.execute(
                    query.order_by(AIInteractionRun.id).limit(
                        CLEANUP_PAGE_SIZE,
                    )
                ).all()
                confirmed_ids = []
                for run_id, client_number, stop_confirmed_at in batch:
                    if stop_confirmed_at is not None:
                        continue
                    item = await run_registry.get(project_id, client_number)
                    if (
                        item is None or item.channel_id != uid
                        or item.channel_type != 251
                    ):
                        confirmed = False
                        continue
                    if item.status == "active":
                        item = await run_registry.request_cancel(item)
                    if item is None or item.status not in {
                        "cancelled", "completed",
                    }:
                        confirmed = False
                    else:
                        confirmed_ids.append(run_id)
                if confirmed_ids:
                    result = db.execute(
                        update(AIInteractionRun).where(
                            AIInteractionRun.project_id == project_uuid,
                            AIInteractionRun.visitor_id == visitor_uuid,
                            AIInteractionRun.id.in_(confirmed_ids),
                            AIInteractionRun.error_message
                            == "visitor_access_ended",
                            AIInteractionRun.stop_confirmed_at.is_(None),
                        ).values(stop_confirmed_at=datetime.now(UTC))
                    )
                    db.commit()
                    if result.rowcount != len(confirmed_ids):
                        confirmed = False
                if len(batch) < CLEANUP_PAGE_SIZE:
                    break
                last_run_id = batch[-1][0]
    except asyncio.CancelledError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        confirmed = False
    disconnected = visitor.im_disconnect_requested_at is not None
    if wukongim_client.enabled and not disconnected:
        token = uuid4()
        try:
            now = datetime.now(UTC)
            claimed = db.execute(
                update(Visitor).where(
                    Visitor.project_id == project_uuid,
                    Visitor.id == visitor_uuid,
                    Visitor.access_retired_at.is_not(None),
                    Visitor.im_disconnect_requested_at.is_(None),
                    or_(
                        Visitor.im_disconnect_lease_until.is_(None),
                        Visitor.im_disconnect_lease_until <= now,
                    ),
                ).values(
                    im_disconnect_lease_until=now + timedelta(
                        seconds=IM_DISCONNECT_LEASE_SECONDS,
                    ),
                    im_disconnect_lease_token=token,
                ).execution_options(synchronize_session=False)
            )
            db.commit()
            if claimed.rowcount == 1:
                try:
                    async with asyncio.timeout(CLEANUP_SECONDS):
                        # Replace direct credentials before disconnecting.
                        await wukongim_client.register_or_login_user(uid=uid)
                        await wukongim_client.kick_user_device(
                            uid=uid, device_flag=-1,
                        )
                    result = db.execute(
                        update(Visitor).where(
                            Visitor.project_id == project_uuid,
                            Visitor.id == visitor_uuid,
                            Visitor.im_disconnect_lease_token == token,
                            Visitor.im_disconnect_requested_at.is_(None),
                        ).values(
                            im_disconnect_requested_at=datetime.now(UTC),
                            im_disconnect_lease_until=None,
                            im_disconnect_lease_token=None,
                        )
                    )
                    db.commit()
                    disconnected = result.rowcount == 1
                except asyncio.CancelledError:
                    db.rollback()
                    _release_im_lease(db, project_uuid, visitor_uuid, token)
                    raise
                except Exception:
                    db.rollback()
                    _release_im_lease(db, project_uuid, visitor_uuid, token)
            else:
                disconnected = visitor.im_disconnect_requested_at is not None
        except asyncio.CancelledError:
            db.rollback()
            raise
        except Exception:
            db.rollback()
    return VisitorAccessEndResponse(
        upstream_stop_status="confirmed" if confirmed else "pending",
        im_disconnect_status="requested" if disconnected else "pending",
    )
