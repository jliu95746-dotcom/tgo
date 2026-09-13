"""Internal user management endpoints."""

from typing import Union
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_user_language, UserLanguage
from app.models import PlatformType
from app.schemas.internal_staff import InternalStaffResponse
from app.schemas.visitor import VisitorResponse, set_visitor_display_nickname
from app.services.internal_staff_service import get_internal_staff_info
from app.api.v1.endpoints.channels import _build_enriched_visitor_payload, _get_visitor_with_relations

router = APIRouter()

# User ID suffixes
STAFF_UID_SUFFIX = "-staff"
VISITOR_UID_SUFFIX = "-vtr"


def parse_user_id(user_id: str) -> tuple[str, str]:
    """Parse user_id and return (user_type, real_id).
    
    Types: 'visitor', 'staff'
    """
    if not isinstance(user_id, str):
        return "visitor", str(user_id)
        
    if user_id.endswith(VISITOR_UID_SUFFIX):
        return "visitor", user_id[:-len(VISITOR_UID_SUFFIX)]
    elif user_id.endswith(STAFF_UID_SUFFIX):
        return "staff", user_id[:-len(STAFF_UID_SUFFIX)]
    else:
        # Backward compatibility: assume visitor if no suffix
        return "visitor", user_id


@router.get(
    "/{user_id}",
    response_model=Union[VisitorResponse, InternalStaffResponse],
    summary="Get enriched user information",
    description="Retrieve comprehensive user data (visitor or staff).",
)
async def get_internal_user_info(
    user_id: str,
    project_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user_language: UserLanguage = Depends(get_user_language),
) -> Union[VisitorResponse, InternalStaffResponse]:
    """
    Retrieve enriched user information for internal services.
    Supports both visitors (with -vtr suffix or no suffix) and staff (with -staff suffix).
    """
    user_type, real_id = parse_user_id(user_id)
    
    if user_type == "staff":
        try:
            staff_uuid = UUID(real_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid staff ID format",
            )
        staff = get_internal_staff_info(db, staff_uuid, project_id)
        if staff is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Staff not found",
            )
        return staff

    # Visitor logic (original logic)
    try:
        visitor_uuid = UUID(real_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid visitor ID format: {real_id}"
        )

    # 1) Get visitor with all relations pre-loaded
    visitor = _get_visitor_with_relations(db, visitor_uuid, project_id)
    if not visitor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Visitor not found"
        )

    # 2) Build enriched payload (tags, system info, recent activities, etc.)
    accept_language = request.headers.get("Accept-Language")
    visitor_payload = _build_enriched_visitor_payload(
        visitor=visitor,
        db=db,
        project_id=project_id,
        accept_language=accept_language,
        user_language=user_language,
    )

    # 3) Set display nickname based on language
    set_visitor_display_nickname(visitor_payload, user_language)

    # 4) Set source_display for AI to know where the visitor comes from
    if visitor.platform:
        if visitor.platform.type == PlatformType.WEBSITE.value:
            parts = []
            if visitor.platform.used_website_title:
                parts.append(visitor.platform.used_website_title)
            if visitor.platform.used_website_url:
                parts.append(f"({visitor.platform.used_website_url})")
            
            if parts:
                visitor_payload.source_display = " ".join(parts)
            else:
                visitor_payload.source_display = visitor.platform.name or "Website"
        else:
            visitor_payload.source_display = visitor.platform.name or visitor.platform.type

    return visitor_payload
