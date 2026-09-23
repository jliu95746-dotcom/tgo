"""Tenant invoices/usage and independent operator review endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.endpoints.company_membership import require_company_features
from app.api.v1.endpoints.operations import require_operator
from app.core.database import get_db
from app.core.security import require_admin, get_current_active_user
from app.models import Staff
from app.models.ai_usage import AIUsageReservation
from app.models.billing import InvoiceRequest
from app.models.billing_reconciliation import BillingReconciliation
from app.models.company_account import AICreditBatch
from app.models.platform_operator import PlatformOperator
from app.schemas.billing_support import (
    CompanyStateChange,
    InvoiceCreate,
    InvoiceProcess,
    InvoiceResponse,
    QuotaBatchResponse,
    QuotaReservationResponse,
    QuotaResolve,
    ReconciliationResponse,
)
from app.services import billing_support as service
from app.services import operations_companies
from app.schemas.operations_companies import CreditAdjustment, OperationsCompany

router = APIRouter(dependencies=[Depends(require_company_features)])
ops_router = APIRouter(
    dependencies=[Depends(require_company_features), Depends(require_operator)]
)


@ops_router.get("/companies", response_model=list[OperationsCompany])
def company_overview(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[OperationsCompany]:
    return operations_companies.companies(db, offset, limit)


@ops_router.post("/companies/{project_id}/credits", status_code=204)
def adjust_company_credits(
    project_id: UUID,
    payload: CreditAdjustment,
    db: Session = Depends(get_db),
    operator: PlatformOperator = Depends(require_operator),
) -> Response:
    operations_companies.adjust_credit(db, operator, project_id, payload)
    db.commit()
    return Response(status_code=204)


@ops_router.get("/reconciliations", response_model=list[ReconciliationResponse])
def reconciliations(
    offset: int = Query(0, ge=0), db: Session = Depends(get_db)
) -> list[BillingReconciliation]:
    return list(
        db.scalars(
            select(BillingReconciliation)
            .order_by(BillingReconciliation.bill_date.desc())
            .offset(offset)
            .limit(100)
        )
    )


@router.post("/invoices", response_model=InvoiceResponse)
def create_invoice(
    payload: InvoiceCreate,
    db: Session = Depends(get_db),
    actor: Staff = Depends(require_admin()),
) -> InvoiceRequest:
    row = service.request_invoice(db, actor, payload)
    db.commit()
    return row


@router.get("/invoices", response_model=list[InvoiceResponse])
def invoices(
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    actor: Staff = Depends(require_admin()),
) -> list[InvoiceRequest]:
    return list(
        db.scalars(
            select(InvoiceRequest)
            .where(InvoiceRequest.project_id == actor.project_id)
            .order_by(InvoiceRequest.created_at.desc())
            .offset(offset)
            .limit(100)
        )
    )


@router.get("/usage/batches", response_model=list[QuotaBatchResponse])
def batches(
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    actor: Staff = Depends(get_current_active_user),
) -> list[AICreditBatch]:
    return list(
        db.scalars(
            select(AICreditBatch)
            .where(AICreditBatch.project_id == actor.project_id)
            .order_by(AICreditBatch.created_at.desc())
            .offset(offset)
            .limit(100)
        )
    )


@router.get("/usage/replies", response_model=list[QuotaReservationResponse])
def replies(
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    actor: Staff = Depends(require_admin()),
) -> list[AIUsageReservation]:
    return list(
        db.scalars(
            select(AIUsageReservation)
            .where(AIUsageReservation.project_id == actor.project_id)
            .order_by(AIUsageReservation.created_at.desc())
            .offset(offset)
            .limit(100)
        )
    )


@ops_router.get("/invoices", response_model=list[InvoiceResponse])
def operator_invoices(
    offset: int = Query(0, ge=0), db: Session = Depends(get_db)
) -> list[InvoiceRequest]:
    return list(
        db.scalars(
            select(InvoiceRequest)
            .order_by(InvoiceRequest.created_at.desc())
            .offset(offset)
            .limit(100)
        )
    )


@ops_router.patch("/invoices/{identifier}", response_model=InvoiceResponse)
def invoice_process(
    identifier: UUID,
    payload: InvoiceProcess,
    db: Session = Depends(get_db),
    operator: PlatformOperator = Depends(require_operator),
) -> InvoiceRequest:
    row = service.process_invoice(db, operator, identifier, payload)
    db.commit()
    return row


@ops_router.post("/companies/{project_id}/state", status_code=204)
def company_state(
    project_id: UUID,
    payload: CompanyStateChange,
    db: Session = Depends(get_db),
    operator: PlatformOperator = Depends(require_operator),
) -> Response:
    service.change_company_state(db, operator, project_id, payload)
    db.commit()
    return Response(status_code=204)


@ops_router.get("/usage/review", response_model=list[QuotaReservationResponse])
def quota_reviews(
    offset: int = Query(0, ge=0), db: Session = Depends(get_db)
) -> list[AIUsageReservation]:
    return list(
        db.scalars(
            select(AIUsageReservation)
            .where(AIUsageReservation.status == "review")
            .order_by(AIUsageReservation.created_at)
            .offset(offset)
            .limit(100)
        )
    )


@ops_router.post("/usage/{identifier}/resolve", status_code=204)
def quota_resolve(
    identifier: UUID,
    payload: QuotaResolve,
    db: Session = Depends(get_db),
    operator: PlatformOperator = Depends(require_operator),
) -> Response:
    service.resolve_quota(db, operator, identifier, payload)
    db.commit()
    return Response(status_code=204)
