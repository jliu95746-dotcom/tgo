"""Project-scoped visitor assignment rule endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_permission
from app.models import DEFAULT_ASSIGNMENT_PROMPT, Staff
from app.schemas import (
    VisitorAssignmentRuleResponse,
    VisitorAssignmentRuleUpdate,
)
from app.services import visitor_assignment_rule_service

router = APIRouter()


@router.get("", response_model=VisitorAssignmentRuleResponse)
async def get_visitor_assignment_rule(
    db: Session = Depends(get_db),
    current_user: Staff = Depends(
        require_permission("visitor_assignment_rules:read")
    ),
) -> VisitorAssignmentRuleResponse:
    """Read saved settings or disabled defaults without creating a rule."""
    return visitor_assignment_rule_service.get_assignment_rule(
        db, current_user.project_id
    )


@router.put("", response_model=VisitorAssignmentRuleResponse)
async def update_visitor_assignment_rule(
    rule_data: VisitorAssignmentRuleUpdate,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(
        require_permission("visitor_assignment_rules:update")
    ),
) -> VisitorAssignmentRuleResponse:
    """Create or partially update the authenticated project's settings."""
    return visitor_assignment_rule_service.update_assignment_rule(
        db, current_user.project_id, rule_data
    )


@router.get("/default-prompt", response_model=dict[str, str])
async def get_default_prompt(
    current_user: Staff = Depends(
        require_permission("visitor_assignment_rules:read")
    ),
) -> dict[str, str]:
    """Return the existing system prompt for authorized staff."""
    return {"default_prompt": DEFAULT_ASSIGNMENT_PROMPT}
