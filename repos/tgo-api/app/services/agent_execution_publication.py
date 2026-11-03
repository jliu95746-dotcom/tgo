"""The publication boundary rechecks the exact producer run, not a new default."""
from typing import Protocol
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.agent_execution import AgentExecutionCheckReceipt, AgentExecutionReference
from app.schemas.knowledge import KnowledgeChannel


class ExecutionOwner(Protocol):
    async def verify_agent_execution(self, reference: AgentExecutionReference) -> AgentExecutionCheckReceipt: ...


async def ensure_execution_can_publish(owner: ExecutionOwner, value: object, *, project_id: str,
                                       agent_id: str | None, channel: str | None) -> None:
    try:
        reference = AgentExecutionReference.model_validate(value)
        if (reference.project_id != UUID(project_id) or reference.channel != KnowledgeChannel(channel or "internal")
            or (agent_id is not None and reference.agent_id != UUID(agent_id))):
            raise ValueError("Mismatched producer scope")
    except (ValidationError, ValueError):
        raise HTTPException(502, "原执行身份无法确认，答复暂停发送，请重新生成或转人工") from None
    receipt = await owner.verify_agent_execution(reference)
    if receipt.reference != reference:
        raise HTTPException(502, "执行核验范围与原答复不一致")
