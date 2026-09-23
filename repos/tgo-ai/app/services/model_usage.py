"""Durable call inventory and rate snapshots; never charge customer reply credits."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import AsyncIterator, Literal
from uuid import UUID, uuid4

from anyio import CancelScope

from app.config import settings
from app.core.logging import get_logger
from app.models.model_usage import ModelUsageRecord
from app.schemas.model_usage import ModelCostRate

logger = get_logger(__name__)


def estimate_cost(
    rate: ModelCostRate | None, input_tokens: int | None, output_tokens: int | None
) -> Decimal | None:
    if rate is None or input_tokens is None or output_tokens is None:
        return None
    amount = (
        Decimal(input_tokens) * rate.input_fen_per_million
        + Decimal(output_tokens) * rate.output_fen_per_million
    ) / Decimal(1000000)
    return amount.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


@dataclass
class UsageTracker:
    input_tokens: int | None = None
    output_tokens: int | None = None
    status: Literal["succeeded", "failed", "cancelled"] = "succeeded"

    def observe(self, metrics: object) -> None:
        for name in ("input_tokens", "output_tokens"):
            value = getattr(metrics, name, None)
            if type(value) is int and 0 <= value <= 2147483647:
                setattr(self, name, value)

    def failed(self) -> None:
        self.status = "failed"


async def save_started(row: ModelUsageRecord) -> None:
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        db.add(row)
        await db.commit()


async def save_finished(
    identifier: UUID, tracker: UsageTracker, rate: ModelCostRate | None
) -> None:
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        row = await db.get(ModelUsageRecord, identifier, with_for_update=True)
        if row is None or row.status != "running":
            return
        row.input_tokens = tracker.input_tokens
        row.output_tokens = tracker.output_tokens
        row.estimated_cost_fen = estimate_cost(
            rate, tracker.input_tokens, tracker.output_tokens
        )
        row.status = tracker.status
        row.completed_at = datetime.now(timezone.utc)
        await db.commit()


@asynccontextmanager
async def track_model_usage(
    project_id: str, model_name: str, purpose: str
) -> AsyncIterator[UsageTracker]:
    from app.services.quota_authorization import (
        current_authorization,
        metered_execution,
    )

    tracker = UsageTracker()
    authorization = current_authorization.get()
    if (
        not settings.saas_enabled
        or not settings.saas_billing_enabled
        or not metered_execution.get()
    ):
        yield tracker
        return
    if authorization is None:
        raise ValueError(
            "Paid model execution is missing its quota authorization context"
        )
    if authorization.project_id != UUID(project_id):
        raise ValueError("Model accounting project does not match authorization")
    rate = settings.saas_model_cost_rates.get(model_name)
    from app.services.platform_models import current_model

    platform = current_model()
    if platform is not None:
        rate = (
            ModelCostRate(
                input_fen_per_million=platform.input_fen_per_million,
                output_fen_per_million=platform.output_fen_per_million,
            )
            if platform.model == model_name
            and platform.input_fen_per_million is not None
            and platform.output_fen_per_million is not None
            else None
        )
    identifier = uuid4()
    row = ModelUsageRecord(
        id=identifier,
        project_id=authorization.project_id,
        reservation_id=authorization.reservation_id,
        model_name=model_name,
        purpose=purpose,
        status="running",
        input_rate_fen=rate.input_fen_per_million if rate else None,
        output_rate_fen=rate.output_fen_per_million if rate else None,
    )
    await save_started(
        row
    )  # If durable inventory is unavailable, do not start a model call.
    try:
        yield tracker
    except (asyncio.CancelledError, GeneratorExit):
        tracker.status = "cancelled"
        raise
    except BaseException:
        tracker.failed()
        raise
    finally:
        with CancelScope(shield=True):
            try:
                await save_finished(identifier, tracker, rate)
            except Exception as exc:
                # The started row remains visible as incomplete, never a zero-cost success.
                logger.error(
                    "Model usage completion could not persist",
                    error_type=type(exc).__name__,
                    usage_id=str(identifier),
                )
