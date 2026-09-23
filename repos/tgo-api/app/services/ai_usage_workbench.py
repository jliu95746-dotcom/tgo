"""Meter explicit staff generations and replay durable results on HTTP retries."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TypeVar
from uuid import UUID

from anyio import CancelScope
from pydantic import BaseModel
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.exceptions import TGOAPIException
from app.models.ai_usage import AIUsageReservation
from app.services import ai_usage
from app.services.ai_usage_runtime import ReplyPermit, _permit, _release
from app.services.company_membership import lock_company

Result = TypeVar("Result", bound=BaseModel)


def prepare(
    project_id: UUID, key: str, fingerprint: str, result_type: type[Result]
) -> ReplyPermit | Result | None:
    with SessionLocal() as db:
        account = lock_company(db, project_id)
        if account is None:
            return None
        ai_usage.require_service(account, datetime.now(timezone.utc))
        previous = db.scalar(
            select(AIUsageReservation).where(
                AIUsageReservation.project_id == project_id,
                AIUsageReservation.round_key == key,
            )
        )
        if previous is not None and previous.receipt:
            if previous.receipt.get("fingerprint") != fingerprint:
                raise TGOAPIException(
                    "同一请求编号不能用于不同内容", code="AI_REQUEST_CONFLICT", status_code=409
                )
            if previous.status == "settled":
                return result_type.model_validate(previous.receipt.get("result"))
        row = ai_usage.reserve(db, project_id, key)
        assert row is not None
        row.receipt = {"kind": "workbench", "fingerprint": fingerprint}
        permit = ReplyPermit(project_id, row.id, row.lease_id, False)
        db.commit()
        return permit


def complete(permit: ReplyPermit, fingerprint: str, result: BaseModel) -> None:
    with SessionLocal() as db:
        ai_usage.begin_publication(
            db,
            permit.project_id,
            permit.reservation_id,
            permit.lease_id,
            {
                "kind": "workbench",
                "fingerprint": fingerprint,
                "result": result.model_dump(mode="json"),
            },
        )
        ai_usage.settle(db, permit.project_id, permit.reservation_id, permit.lease_id)
        db.commit()


async def generate_workbench(
    project_id: UUID,
    key: str,
    fingerprint: str,
    generate: Callable[[], Awaitable[Result]],
    result_type: type[Result],
) -> Result:
    if not settings.SAAS_ENABLED or not settings.SAAS_BILLING_ENABLED:
        return await generate()
    prepared = await asyncio.to_thread(
        prepare, project_id, key, fingerprint, result_type
    )
    if isinstance(prepared, BaseModel):
        return result_type.model_validate(prepared)
    token = _permit.set(prepared)
    try:
        result = await generate()
        if prepared is not None:
            with CancelScope(shield=True):
                await asyncio.to_thread(complete, prepared, fingerprint, result)
        return result
    finally:
        try:
            if prepared is not None:
                with CancelScope(shield=True):
                    await asyncio.to_thread(_release, prepared)
        finally:
            _permit.reset(token)
