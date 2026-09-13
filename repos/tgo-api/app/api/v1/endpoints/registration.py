"""Public signup, separate from authenticated staff management."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.config import settings
from app.schemas.registration import RegistrationRequest
from app.schemas.staff import StaffResponse
from app.services.project_registration import register_project_account
from app.services.registration_limit import limit_registration

router = APIRouter()


@router.post("/register", response_model=StaffResponse, status_code=201)
async def register_staff(
    payload: RegistrationRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> StaffResponse:
    if not settings.PUBLIC_REGISTRATION_ENABLED:
        raise HTTPException(403, "Public registration is disabled")
    # Use the trusted server peer, never an arbitrary user-supplied forwarding header.
    await limit_registration(request.client.host if request.client else "unknown")
    return await register_project_account(db, payload)
