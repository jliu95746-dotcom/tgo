"""Reconcile workflow quota against the owning service's durable record."""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.models.ai_usage import AIUsageReservation
from app.schemas.workflow_ai_usage import WorkflowUsageReceipt
from app.services import ai_usage, workflow_ai_usage
from app.services.ai_usage_runtime import ReplyPermit
from app.services.workflow_client import workflow_client

logger = get_logger(__name__)


def _pending() -> list[tuple[ReplyPermit, WorkflowUsageReceipt]]:
    now = datetime.now(timezone.utc)
    corrupt: list[ReplyPermit] = []
    with SessionLocal() as db:
        rows = db.scalars(
            select(AIUsageReservation)
            .where(
                AIUsageReservation.receipt["kind"].as_string() == "workflow",
                or_(
                    AIUsageReservation.status == "reserved",
                    (AIUsageReservation.status == "review")
                    & (AIUsageReservation.expires_at <= now),
                ),
            )
            .order_by(AIUsageReservation.expires_at)
            .limit(100)
        )
        result: list[tuple[ReplyPermit, WorkflowUsageReceipt]] = []
        for row in rows:
            try:
                receipt = WorkflowUsageReceipt.model_validate(
                    {
                        key: value
                        for key, value in (row.receipt or {}).items()
                        if key != "workflow_ai_calls"
                    }
                )
            except ValueError:
                corrupt.append(
                    ReplyPermit(row.project_id, row.id, row.lease_id, False)
                )
                continue
            result.append(
                (
                    ReplyPermit(row.project_id, row.id, row.lease_id, False),
                    receipt,
                )
            )
        db.commit()
    for permit in corrupt:
        try:
            _defer(permit)
        except Exception as error:
            logger.warning(
                "Workflow AI invalid receipt deferral failed: %s",
                type(error).__name__,
            )
    return result


def _defer(permit: ReplyPermit) -> None:
    with SessionLocal() as db:
        row = ai_usage.owned(
            db, permit.project_id, permit.reservation_id, permit.lease_id
        )
        if row.status not in {"settled", "released"}:
            row.status = "review"
            row.expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        db.commit()


async def reconcile_workflows() -> None:
    for permit, receipt in await asyncio.to_thread(_pending):
        try:
            outcome = await workflow_client.workflow_usage_outcome(
                permit.project_id, receipt.execution_id
            )
            await asyncio.to_thread(workflow_ai_usage.finish, permit, outcome)
        except Exception as error:
            logger.warning(
                "Workflow AI quota reconciliation failed: %s",
                type(error).__name__,
            )
            try:
                await asyncio.to_thread(_defer, permit)
            except Exception as stale:
                logger.warning(
                    "Workflow AI quota deferral failed: %s",
                    type(stale).__name__,
                )
