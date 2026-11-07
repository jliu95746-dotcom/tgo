"""Internal management-only setup snapshots, with login identity from API."""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.agent_trials import (
    PrivateTrialRoute,
    TrialPrincipal,
    require_trial_principal,
)
from app.database import get_db
from app.schemas.agent_drafts import AgentDraftReceipt, AgentDraftRequest
from app.services.agent_drafts import AgentDraftService

router = APIRouter(route_class=PrivateTrialRoute)


def get_service(db: AsyncSession = Depends(get_db)) -> AgentDraftService:
    return AgentDraftService(db)


@router.post("/{draft_id}", response_model=AgentDraftReceipt)
async def save(
    draft_id: UUID,
    payload: AgentDraftRequest,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentDraftService = Depends(get_service),
) -> AgentDraftReceipt:
    return await service.save(
        principal.project_id, principal.staff_id, draft_id, payload
    )


@router.get("/{draft_id}", response_model=AgentDraftReceipt)
async def get(
    draft_id: UUID,
    principal: TrialPrincipal = Depends(require_trial_principal),
    service: AgentDraftService = Depends(get_service),
) -> AgentDraftReceipt:
    return await service.get(
        principal.project_id, principal.staff_id, draft_id
    )
