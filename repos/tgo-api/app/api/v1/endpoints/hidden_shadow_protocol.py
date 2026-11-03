"""Administrator-only registration of independent G4 review samples."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_admin
from app.models import Staff
from app.models.hidden_shadow_protocol import HiddenShadowProtocol
from app.schemas.hidden_shadow_protocol import (
    HiddenShadowProtocolCasePage,
    HiddenShadowProtocolCreateRequest,
    HiddenShadowProtocolIntegrityResponse,
    HiddenShadowProtocolList,
    HiddenShadowProtocolResponse,
)
from app.schemas.hidden_shadow_review import (
    HiddenShadowCaseDetail, HiddenShadowReviewReceipt,
    HiddenShadowReviewRequest, HiddenShadowReviewSummary,
    HiddenShadowSourceMessage,
)
from app.services.wukongim_client import wukongim_client
from app.schemas.hidden_shadow_adjudication import (
    HiddenShadowAdjudicationReceipt, HiddenShadowAdjudicationRequest,
    HiddenShadowReviewPair,
)
from app.schemas.hidden_shadow_gate_assessment import (
    HiddenShadowGateAssessment,
)
from app.schemas.hidden_shadow_gate_signoff import (
    HiddenShadowGateSignoffReceipt,
)
from app.schemas.shadow_gate_signoff import (
    ShadowGateDecisionRequest, ShadowGateSignoffRequest,
)
from app.services.hidden_shadow_protocol import HiddenShadowProtocolService
from app.services.hidden_shadow_review import HiddenShadowReviewService
from app.services.hidden_shadow_adjudication import (
    HiddenShadowAdjudicationService,
)
from app.services.hidden_shadow_gate_assessment import (
    HiddenShadowGateAssessmentService,
)
from app.services.hidden_shadow_gate_signoff import (
    HiddenShadowGateSignoffService,
)


router = APIRouter()


def _response(row: HiddenShadowProtocol) -> HiddenShadowProtocolResponse:
    status: Literal["scheduled", "frozen", "empty", "cancelled"] = (
        "cancelled" if row.cancelled_at is not None
        else "scheduled" if row.finalized_at is None
        else "frozen" if row.sample_count else "empty"
    )
    return HiddenShadowProtocolResponse(
        id=row.id,
        request_id=row.request_id,
        created_at=row.created_at,
        window_start=row.window_start,
        window_end=row.window_end,
        sample_size=row.sample_size,
        algorithm=row.algorithm,
        seed_sha256=row.seed_sha256,
        random_seed=row.random_seed if row.finalized_at else None,
        status=status,
        finalized_at=row.finalized_at,
        cancelled_at=row.cancelled_at,
        cancelled_by=row.cancelled_by,
        population_count=row.population_count,
        sample_count=row.sample_count,
        population_sha256=row.population_sha256,
        manifest_sha256=row.manifest_sha256,
    )


@router.post("", response_model=HiddenShadowProtocolResponse, status_code=201)
def register_hidden_shadow_protocol(
    request: HiddenShadowProtocolCreateRequest,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> HiddenShadowProtocolResponse:
    response.headers["Cache-Control"] = "no-store"
    row = HiddenShadowProtocolService(db).register(
        project_id=current_user.project_id,
        creator_id=current_user.id,
        request_id=request.request_id,
        window_start=request.window_start,
        window_end=request.window_end,
        sample_size=request.sample_size,
    )
    return _response(row)


@router.get("", response_model=HiddenShadowProtocolList)
def list_hidden_shadow_protocols(
    response: Response,
    limit: int = Query(default=20, ge=1, le=100),
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> HiddenShadowProtocolList:
    response.headers["Cache-Control"] = "no-store"
    rows, has_more = HiddenShadowProtocolService(db).list(
        current_user.project_id, limit=limit,
    )
    return HiddenShadowProtocolList(
        items=tuple(_response(row) for row in rows),
        has_more=has_more,
    )


@router.get("/{protocol_id}", response_model=HiddenShadowProtocolResponse)
def get_hidden_shadow_protocol(
    protocol_id: UUID,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> HiddenShadowProtocolResponse:
    response.headers["Cache-Control"] = "no-store"
    row = HiddenShadowProtocolService(db).get(
        current_user.project_id, protocol_id,
    )
    return _response(row)


@router.post(
    "/{protocol_id}/finalize",
    response_model=HiddenShadowProtocolResponse,
)
def finalize_hidden_shadow_protocol(
    protocol_id: UUID,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> HiddenShadowProtocolResponse:
    response.headers["Cache-Control"] = "no-store"
    row = HiddenShadowProtocolService(db).finalize(
        current_user.project_id, protocol_id,
    )
    return _response(row)


@router.post(
    "/{protocol_id}/cancel",
    response_model=HiddenShadowProtocolResponse,
)
def cancel_hidden_shadow_protocol(
    protocol_id: UUID,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> HiddenShadowProtocolResponse:
    response.headers["Cache-Control"] = "no-store"
    row = HiddenShadowProtocolService(db).cancel(
        current_user.project_id, protocol_id, actor_id=current_user.id,
    )
    return _response(row)


@router.get(
    "/{protocol_id}/cases", response_model=HiddenShadowProtocolCasePage,
)
def list_hidden_shadow_cases(
    protocol_id: UUID,
    response: Response,
    limit: int = Query(default=100, ge=1, le=200),
    after_candidate_id: UUID | None = None,
    selected_only: bool = True,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> HiddenShadowProtocolCasePage:
    response.headers["Cache-Control"] = "no-store"
    return HiddenShadowProtocolService(db).list_cases(
        current_user.project_id, protocol_id,
        limit=limit,
        after_candidate_id=after_candidate_id,
        selected_only=selected_only,
    )


@router.get(
    "/{protocol_id}/integrity",
    response_model=HiddenShadowProtocolIntegrityResponse,
)
def verify_hidden_shadow_protocol_integrity(
    protocol_id: UUID,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> HiddenShadowProtocolIntegrityResponse:
    response.headers["Cache-Control"] = "no-store"
    return HiddenShadowProtocolService(db).verify_integrity(
        current_user.project_id, protocol_id,
    )


@router.get(
    "/{protocol_id}/cases/{candidate_id}",
    response_model=HiddenShadowCaseDetail,
)
def get_hidden_shadow_review_case(
    protocol_id: UUID,
    candidate_id: UUID,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> HiddenShadowCaseDetail:
    response.headers["Cache-Control"] = "no-store"
    return HiddenShadowReviewService(db).get_case(
        current_user.project_id, protocol_id, candidate_id,
    )


@router.get(
    "/{protocol_id}/cases/{candidate_id}/source-message",
    response_model=HiddenShadowSourceMessage,
)
async def get_hidden_shadow_source_message(
    protocol_id: UUID,
    candidate_id: UUID,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> HiddenShadowSourceMessage:
    response.headers["Cache-Control"] = "no-store"
    return await HiddenShadowReviewService(db).get_source_message(
        current_user.project_id, protocol_id, candidate_id,
        im_client=wukongim_client,
    )


@router.post(
    "/{protocol_id}/cases/{candidate_id}/reviews",
    response_model=HiddenShadowReviewReceipt,
    status_code=201,
)
async def submit_hidden_shadow_review(
    protocol_id: UUID,
    candidate_id: UUID,
    request: HiddenShadowReviewRequest,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> HiddenShadowReviewReceipt:
    response.headers["Cache-Control"] = "no-store"
    service = HiddenShadowReviewService(db)
    previous = service.replay(
        current_user.project_id, protocol_id, candidate_id,
        current_user.id, request,
    )
    if previous is not None:
        return previous
    source_proof = await service.get_source_message(
        current_user.project_id, protocol_id, candidate_id,
        im_client=wukongim_client,
    )
    return service.submit(
        current_user.project_id, protocol_id, candidate_id,
        current_user.id, request, source_proof=source_proof,
    )


@router.get(
    "/{protocol_id}/cases/{candidate_id}/my-review",
    response_model=HiddenShadowReviewReceipt | None,
)
def get_hidden_shadow_own_review(
    protocol_id: UUID,
    candidate_id: UUID,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> HiddenShadowReviewReceipt | None:
    response.headers["Cache-Control"] = "no-store"
    return HiddenShadowReviewService(db).get_own_review(
        current_user.project_id, protocol_id, candidate_id,
        current_user.id,
    )


@router.get(
    "/{protocol_id}/review-summary",
    response_model=HiddenShadowReviewSummary,
)
def get_hidden_shadow_review_summary(
    protocol_id: UUID,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> HiddenShadowReviewSummary:
    response.headers["Cache-Control"] = "no-store"
    return HiddenShadowReviewService(db).summary(
        current_user.project_id, protocol_id,
    )


@router.get(
    "/{protocol_id}/gate-readiness",
    response_model=HiddenShadowGateAssessment,
)
def get_hidden_shadow_gate_readiness(
    protocol_id: UUID,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> HiddenShadowGateAssessment:
    response.headers["Cache-Control"] = "no-store"
    return HiddenShadowGateAssessmentService(db).assess(
        current_user.project_id, protocol_id,
    )


@router.get(
    "/{protocol_id}/signoff",
    response_model=HiddenShadowGateSignoffReceipt | None,
)
def get_hidden_shadow_gate_signoff(
    protocol_id: UUID,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> HiddenShadowGateSignoffReceipt | None:
    response.headers["Cache-Control"] = "no-store"
    return HiddenShadowGateSignoffService(db).get(
        current_user.project_id, protocol_id,
    )


@router.post(
    "/{protocol_id}/signoff",
    response_model=HiddenShadowGateSignoffReceipt,
    status_code=201,
)
def request_hidden_shadow_gate_signoff(
    protocol_id: UUID,
    request: ShadowGateSignoffRequest,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> HiddenShadowGateSignoffReceipt:
    response.headers["Cache-Control"] = "no-store"
    return HiddenShadowGateSignoffService(db).request(
        current_user.project_id, protocol_id, current_user.id, request,
    )


@router.post(
    "/{protocol_id}/signoff/decision",
    response_model=HiddenShadowGateSignoffReceipt,
)
def decide_hidden_shadow_gate_signoff(
    protocol_id: UUID,
    request: ShadowGateDecisionRequest,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> HiddenShadowGateSignoffReceipt:
    response.headers["Cache-Control"] = "no-store"
    return HiddenShadowGateSignoffService(db).decide(
        current_user.project_id, protocol_id, current_user.id, request,
    )


@router.get(
    "/{protocol_id}/cases/{candidate_id}/reviews",
    response_model=HiddenShadowReviewPair,
)
def get_hidden_shadow_review_pair(
    protocol_id: UUID,
    candidate_id: UUID,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> HiddenShadowReviewPair:
    response.headers["Cache-Control"] = "no-store"
    return HiddenShadowAdjudicationService(db).review_pair(
        current_user.project_id, protocol_id, candidate_id,
    )


@router.post(
    "/{protocol_id}/cases/{candidate_id}/adjudication",
    response_model=HiddenShadowAdjudicationReceipt,
    status_code=201,
)
def submit_hidden_shadow_adjudication(
    protocol_id: UUID,
    candidate_id: UUID,
    request: HiddenShadowAdjudicationRequest,
    response: Response,
    current_user: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> HiddenShadowAdjudicationReceipt:
    response.headers["Cache-Control"] = "no-store"
    return HiddenShadowAdjudicationService(db).submit(
        current_user.project_id, protocol_id, candidate_id,
        current_user.id, request,
    )
