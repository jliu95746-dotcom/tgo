"""Public holder-authorized visitor lifecycle operations."""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.visitor_access import (
    VisitorAccessEndRequest,
    VisitorAccessEndResponse,
)
from app.services.visitor_access_end import end_owned_access

router = APIRouter()


@router.post("/access/end", response_model=VisitorAccessEndResponse)
async def end_visitor_access(
    request: VisitorAccessEndRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> VisitorAccessEndResponse:
    result = await end_owned_access(db, request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return result
