"""Shared configuration inputs and private, resumable setup drafts."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user, require_admin
from app.models import Staff
from app.schemas.agent_trial_reviews import KnowledgeReviewCase
from app.services.knowledge_review_cases import list_cases
from app.schemas.knowledge import KnowledgeChannel
from app.schemas.scenario_resources import ScenarioResourceCatalog
from app.services.scenario_resources import load_catalog
from app.schemas.scenario_readiness import (
    ScenarioReadinessRequest,
    ScenarioReadinessReport,
)
from app.services.scenario_readiness import check_scenario
from app.schemas.scenario_drafts import (
    ScenarioDraftCreate,
    ScenarioDraftResponse,
    ScenarioDraftUpdate,
    ScenarioModule,
    ScenarioTemplate,
)
from app.services import scenario_drafts, scenario_templates
from app.services import scenario_agents
from app.schemas.scenario_agents import ScenarioAgentReceipt, ScenarioAgentSave

router = APIRouter()


@router.get("/knowledge-review-cases", response_model=list[KnowledgeReviewCase])
def knowledge_review_cases(current_user: Staff = Depends(require_admin())) -> list[KnowledgeReviewCase]:
    return list_cases()


@router.post("/drafts/{draft_id}/agent", response_model=ScenarioAgentReceipt)
async def save_agent(
    draft_id: UUID, request: ScenarioAgentSave,
    current_user: Staff = Depends(require_admin()), db: Session = Depends(get_db),
) -> ScenarioAgentReceipt:
    return await scenario_agents.save(db, current_user.project_id, current_user.id,
                                     draft_id, request.expected_revision)


@router.get("/drafts/{draft_id}/agent", response_model=ScenarioAgentReceipt)
async def get_agent(
    draft_id: UUID, current_user: Staff = Depends(require_admin()), db: Session = Depends(get_db),
) -> ScenarioAgentReceipt:
    return await scenario_agents.get(db, current_user.project_id, current_user.id, draft_id)


@router.get("/templates", response_model=list[ScenarioTemplate])
def get_templates(
    module: ScenarioModule | None = None,
    current_user: Staff = Depends(get_current_active_user),
) -> list[ScenarioTemplate]:
    return scenario_templates.list_templates(module)


@router.post("/drafts", response_model=ScenarioDraftResponse, status_code=201)
def create_draft(
    request: ScenarioDraftCreate,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ScenarioDraftResponse:
    return scenario_drafts.create_draft(
        db, current_user.project_id, current_user.id, request
    )


@router.get("/drafts", response_model=list[ScenarioDraftResponse])
def list_drafts(
    module: ScenarioModule | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: Staff = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> list[ScenarioDraftResponse]:
    return scenario_drafts.list_drafts(
        db, current_user.project_id, current_user.id, module, limit, offset
    )


@router.get("/drafts/{draft_id}", response_model=ScenarioDraftResponse)
def get_draft(
    draft_id: UUID,
    current_user: Staff = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> ScenarioDraftResponse:
    return scenario_drafts.get_draft(
        db, current_user.project_id, current_user.id, draft_id
    )


@router.patch("/drafts/{draft_id}", response_model=ScenarioDraftResponse)
def update_draft(
    draft_id: UUID,
    request: ScenarioDraftUpdate,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> ScenarioDraftResponse:
    return scenario_drafts.update_draft(
        db, current_user.project_id, current_user.id, draft_id, request
    )


@router.post("/readiness", response_model=ScenarioReadinessReport)
async def check_readiness(
    request: ScenarioReadinessRequest,
    current_user: Staff = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> ScenarioReadinessReport:
    return await check_scenario(db, current_user.project_id, request)


@router.get("/resources", response_model=ScenarioResourceCatalog)
async def get_resources(
    channel: KnowledgeChannel = KnowledgeChannel.WEB,
    current_user: Staff = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> ScenarioResourceCatalog:
    return await load_catalog(db, current_user.project_id, channel)
