"""Bind one durable quota permit to the complete multi-phase reply execution."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

from anyio import CancelScope
from pydantic import JsonValue
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import Platform, Visitor
from app.models.ai_interaction_run import AIInteractionRun
from app.services import ai_usage


@dataclass(frozen=True)
class ReplyPermit:
    project_id: UUID
    reservation_id: UUID
    lease_id: UUID
    external: bool


_permit: ContextVar[ReplyPermit | None] = ContextVar("ai_reply_quota", default=None)


def current_permit() -> ReplyPermit | None:
    return _permit.get()


def authorization_headers() -> dict[str, str]:
    if not settings.SAAS_ENABLED or not settings.SAAS_BILLING_ENABLED:
        return {}
    headers: dict[str, str] = {}
    if settings.SAAS_INTERNAL_TOKEN is not None:
        headers[
            "X-SaaS-Service-Token"
        ] = settings.SAAS_INTERNAL_TOKEN.get_secret_value()
    permit = current_permit()
    if permit is not None:
        headers["X-AI-Reservation"] = str(permit.reservation_id)
        headers["X-AI-Lease"] = str(permit.lease_id)
    return headers


def _reserve(
    project_id: UUID, client_msg_no: str, channel_id: str, channel_type: int
) -> ReplyPermit | None:
    with SessionLocal() as db:
        row = ai_usage.reserve(db, project_id, f"reply:{client_msg_no}")
        if row is None:
            db.commit()
            return None
        external = False
        receipt: dict[str, JsonValue] = {
            "channel_id": channel_id,
            "channel_type": channel_type,
            "client_msg_no": client_msg_no,
            "kind": "im",
        }
        if channel_type == 251:
            visitor = db.get(Visitor, UUID(channel_id.removesuffix("-vtr")))
            platform = db.get(Platform, visitor.platform_id) if visitor else None
            if visitor is None or visitor.project_id != project_id or platform is None:
                raise ValueError("Invalid quota channel")
            external = (
                platform.type != "website" and visitor.platform_open_id != channel_id
            )
            if external:
                run = db.scalar(
                    select(AIInteractionRun).where(
                        AIInteractionRun.project_id == project_id,
                        AIInteractionRun.response_client_msg_no == client_msg_no,
                    )
                )
                if run is None or platform.type != "wecom":
                    raise ValueError(
                        "This SaaS channel has no verified billing delivery adapter"
                    )
                receipt.update(
                    kind="wecom",
                    platform_id=str(platform.id),
                    source_message_id=run.source_message_id,
                )
        row.receipt = receipt
        result = ReplyPermit(project_id, row.id, row.lease_id, external)
        db.commit()
        return result


@asynccontextmanager
async def metered_reply(
    project_id: str, client_msg_no: str, channel_id: str, channel_type: int
) -> AsyncIterator[None]:
    if not settings.SAAS_ENABLED or not settings.SAAS_BILLING_ENABLED:
        yield
        return
    permit = await asyncio.to_thread(
        _reserve, UUID(project_id), client_msg_no, channel_id, channel_type
    )
    token = _permit.set(permit)
    try:
        yield
    finally:
        try:
            if permit is not None:
                with CancelScope(shield=True):
                    await asyncio.to_thread(_release, permit)
        finally:
            _permit.reset(token)


def _release(permit: ReplyPermit) -> None:
    with SessionLocal() as db:
        ai_usage.release(db, permit.project_id, permit.reservation_id, permit.lease_id)
        db.commit()


async def begin_delivery() -> None:
    permit = current_permit()
    if permit is None:
        return

    def begin() -> None:
        with SessionLocal() as db:
            row = ai_usage.owned(
                db, permit.project_id, permit.reservation_id, permit.lease_id
            )
            ai_usage.begin_publication(
                db,
                permit.project_id,
                permit.reservation_id,
                permit.lease_id,
                row.receipt or {},
            )
            db.commit()

    await asyncio.to_thread(begin)


async def confirm_im_delivery() -> None:
    permit = current_permit()
    if permit is None or permit.external:
        return

    def confirm() -> None:
        with SessionLocal() as db:
            ai_usage.settle(
                db, permit.project_id, permit.reservation_id, permit.lease_id
            )
            db.commit()

    await asyncio.to_thread(confirm)
