"""Private durable-turn dispatch; the core API owns the final send permission."""

from uuid import UUID
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.billing_receipts import require_service
from app.db.base import get_db
from app.db.models import Platform
from app.db.turn_context import CustomerTurnContext
from app.domain.services.turn_context import unseal
from app.domain.services.adapters.base import DeliveryRejected, SimpleStdoutAdapter
from app.domain.services.dispatcher import select_adapter_for_target

router = APIRouter(prefix="/internal/turns", dependencies=[Depends(require_service)])


class DispatchRequest(BaseModel):
    project_id: UUID
    platform_id: UUID
    turn_id: UUID
    version: int = Field(ge=1)
    channel_id: str
    source_message_id: str = Field(min_length=1, max_length=255)


class DispatchResult(BaseModel):
    state: str


@router.post("/deliver", response_model=DispatchResult)
async def deliver(
    body: DispatchRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> DispatchResult:
    platform = await db.scalar(
        select(Platform).where(
            Platform.id == body.platform_id,
            Platform.project_id == body.project_id,
            Platform.is_active.is_(True),
        )
    )
    if platform is None or platform.type in {"wechat_local", "website"}:
        raise HTTPException(403, "Invalid durable channel target")
    row = await db.get(CustomerTurnContext, (platform.id, body.source_message_id))
    if row is None:
        raise HTTPException(409, "Original channel routing has not been registered")
    if db.get_bind().dialect.name == "postgresql":
        number = int.from_bytes(bytes.fromhex(row.actor_key[:16]), "big", signed=True)
        await db.execute(
            sql_text("SELECT pg_advisory_xact_lock(:key)"), {"key": number}
        )
    await db.refresh(row)
    message = unseal(row, platform)
    pending = await db.scalar(
        select(CustomerTurnContext.source_message_id)
        .where(
            CustomerTurnContext.actor_key == row.actor_key,
            CustomerTurnContext.registered_at.is_(None),
        )
        .limit(1)
    )
    if pending is not None:
        raise HTTPException(409, "New customer messages are waiting for registration")
    adapter = await select_adapter_for_target(message, platform=platform)
    client = request.app.state.tgo_api_client
    args = {
        "platform_api_key": platform.api_key,
        "channel_id": body.channel_id,
        "channel_type": 251,
        "version": body.version,
    }
    try:
        claim = await client.turn_action(str(body.turn_id), "claim", args)
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 409:
            return DispatchResult(state="superseded")
        raise
    state, proof = "unknown", None
    try:
        if (
            claim.get("source_message_id") != body.source_message_id
            or claim.get("customer_uid") != message.from_uid
        ):
            raise DeliveryRejected("Original sender or source could not be verified")
        if isinstance(adapter, SimpleStdoutAdapter):
            raise DeliveryRejected("Channel has no delivery adapter")
        text = claim.get("text")
        if not isinstance(text, str) or not text:
            raise DeliveryRejected("Reply is empty")
        await adapter.send_final({"text": text, "delivery_fenced": True})
        proof = getattr(adapter, "delivery_receipt", None)
        state = "sent" if proof else "unknown"
    except DeliveryRejected:
        state = "rejected"
    except Exception:
        # A timed-out provider request may already have sent. Report uncertainty,
        # leaving any retry decision to explicit receipt reconciliation.
        state = "unknown"
    finally:
        await client.turn_action(
            str(body.turn_id),
            "receipt",
            {**args, "claim_id": claim["claim_id"], "result": state, "receipt": proof},
        )
    return DispatchResult(state=state)
