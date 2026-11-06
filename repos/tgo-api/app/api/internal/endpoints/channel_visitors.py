"""Channel registration exists only on the authenticated private listener."""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from uuid import UUID

from app.api.internal.endpoints.ai_usage import (
    require_quota_service as require_channel_service,
)
from app.core.database import get_db
from app.schemas.channel_visitor_registration import (
    ChannelVisitorRegistrationInput,
    ChannelVisitorRegistrationResult,
)
from app.services.channel_visitor_registration import register_channel_visitor
from app.services.business_customer_identity import (
    BusinessCustomerIdentityService,
)
from app.schemas.business_customer_identity import (
    CustomerIdentityExchangeRequest,
    CustomerIdentitySession,
)

router = APIRouter(dependencies=[Depends(require_channel_service)])


@router.post(
    "/register",
    response_model=ChannelVisitorRegistrationResult,
    status_code=201,
)
async def register(
    payload: ChannelVisitorRegistrationInput,
    response: Response,
    db: Session = Depends(get_db),
) -> ChannelVisitorRegistrationResult:
    response.headers["Cache-Control"] = "no-store"
    return await register_channel_visitor(db, payload)


@router.post(
    "/{connection_id}/customer-identities/exchange",
    response_model=CustomerIdentitySession,
)
def exchange(
    connection_id: UUID,
    payload: CustomerIdentityExchangeRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> CustomerIdentitySession:
    response.headers["Cache-Control"] = "no-store"
    return BusinessCustomerIdentityService(db).exchange(
        connection_id, payload.merchant_assertion, trusted_channel=True
    )
