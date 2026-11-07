"""Only merchant-signed assertions create sessions; admin requests manage policy."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.company_configuration_access import require_configuration_write
from app.core.database import get_db
from app.core.security import require_admin
from app.models.staff import Staff
from app.schemas.business_customer_identity import (
    CustomerIdentityBindingView,
    CustomerIdentityExchangeRequest,
    CustomerIdentityPolicyView,
    CustomerIdentityPolicyWrite,
    CustomerIdentityRevisionRequest,
    CustomerIdentitySession,
)
from app.services.business_customer_identity import (
    BusinessCustomerIdentityService,
)
from app.api.business_trial_route import BusinessTrialRoute

router = APIRouter(route_class=BusinessTrialRoute)


def get_service(
    db: Session = Depends(get_db),
) -> BusinessCustomerIdentityService:
    return BusinessCustomerIdentityService(db)


@router.get(
    "/{connection_id}/customer-identity-policy",
    response_model=CustomerIdentityPolicyView,
)
def read_policy(
    connection_id: UUID,
    actor: Staff = Depends(require_admin()),
    service: BusinessCustomerIdentityService = Depends(get_service),
) -> CustomerIdentityPolicyView:
    return service.read_policy(actor.project_id, connection_id)


@router.put(
    "/{connection_id}/customer-identity-policy",
    response_model=CustomerIdentityPolicyView,
    dependencies=[Depends(require_configuration_write)],
)
def configure_policy(
    connection_id: UUID,
    payload: CustomerIdentityPolicyWrite,
    actor: Staff = Depends(require_admin()),
    service: BusinessCustomerIdentityService = Depends(get_service),
) -> CustomerIdentityPolicyView:
    return service.configure(
        actor.project_id, actor.id, connection_id, payload
    )


@router.post(
    "/{connection_id}/customer-identities/exchange",
    response_model=CustomerIdentitySession,
)
def exchange_assertion(
    connection_id: UUID,
    payload: CustomerIdentityExchangeRequest,
    response: Response,
    service: BusinessCustomerIdentityService = Depends(get_service),
) -> CustomerIdentitySession:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return service.exchange(
        connection_id,
        payload.merchant_assertion,
        visitor_access_token=payload.visitor_access_token,
    )


@router.get(
    "/{connection_id}/customer-identities",
    response_model=list[CustomerIdentityBindingView],
)
def list_bindings(
    connection_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    actor: Staff = Depends(require_admin()),
    service: BusinessCustomerIdentityService = Depends(get_service),
) -> list[CustomerIdentityBindingView]:
    return service.list_bindings(actor.project_id, connection_id, limit)


@router.post(
    "/{connection_id}/customer-identities/{binding_id}/revoke",
    response_model=CustomerIdentityBindingView,
)
def revoke_identity(
    connection_id: UUID,
    binding_id: UUID,
    payload: CustomerIdentityRevisionRequest,
    actor: Staff = Depends(require_admin()),
    service: BusinessCustomerIdentityService = Depends(get_service),
) -> CustomerIdentityBindingView:
    return service.revoke(
        actor.project_id,
        actor.id,
        connection_id,
        binding_id,
        payload.expected_revision,
    )
