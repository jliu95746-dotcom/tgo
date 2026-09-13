"""Private AI completion receipts; possession of the exact phase is required."""

from fastapi import APIRouter, HTTPException

from app.schemas.reply_phase import (
    ReplyPhaseAcknowledgment,
    ReplyPhaseIdentity,
    ReplyPhaseReceipt,
)
from app.services import ai_reply_control as control
from app.services.run_registry import RegistryUnavailable

router = APIRouter()


@router.post("/ended", response_model=ReplyPhaseAcknowledgment)
async def confirm_ended_phase(receipt: ReplyPhaseReceipt) -> ReplyPhaseAcknowledgment:
    identity = ReplyPhaseIdentity.model_validate(receipt.model_dump(exclude={"status"}))
    try:
        result = await control.run_registry.end_phase(identity)
    except RegistryUnavailable as exc:
        raise HTTPException(503, "回复状态服务暂时不可用") from exc
    if result is None:
        raise HTTPException(404, "没有找到匹配的回复阶段")
    return ReplyPhaseAcknowledgment(phase_id=receipt.phase_id)
