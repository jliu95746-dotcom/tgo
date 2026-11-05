"""Project-scoped reception settings and their safe initial defaults."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models import (
    AIProvider,
    DEFAULT_ASSIGNMENT_PROMPT,
    VisitorAssignmentRule,
)
from app.schemas.visitor_assignment_rule import (
    VisitorAssignmentRuleCreate,
    VisitorAssignmentRuleResponse,
    VisitorAssignmentRuleUpdate,
)

logger = get_logger("services.visitor_assignment_rule")


def _default_configuration() -> VisitorAssignmentRuleCreate:
    """Use one validated configuration for both preview and first save."""
    weekdays = [
        int(day.strip())
        for day in settings.ASSIGNMENT_RULE_DEFAULT_WEEKDAYS.split(",")
        if day.strip()
    ]
    return VisitorAssignmentRuleCreate(
        llm_assignment_enabled=False,
        timezone=settings.ASSIGNMENT_RULE_DEFAULT_TIMEZONE,
        service_weekdays=weekdays,
        service_start_time=settings.ASSIGNMENT_RULE_DEFAULT_START_TIME,
        service_end_time=settings.ASSIGNMENT_RULE_DEFAULT_END_TIME,
        max_concurrent_chats=(
            settings.ASSIGNMENT_RULE_DEFAULT_MAX_CONCURRENT_CHATS
        ),
        auto_close_hours=settings.ASSIGNMENT_RULE_DEFAULT_AUTO_CLOSE_HOURS,
    )


def get_assignment_rule(
    db: Session,
    project_id: UUID,
) -> VisitorAssignmentRuleResponse:
    """Read settings without writing a default row on page load."""
    rule = (
        db.query(VisitorAssignmentRule)
        .filter(VisitorAssignmentRule.project_id == project_id)
        .first()
    )
    if rule is not None:
        return VisitorAssignmentRuleResponse.model_validate(rule)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return VisitorAssignmentRuleResponse(
        id=uuid4(),
        project_id=project_id,
        effective_prompt=DEFAULT_ASSIGNMENT_PROMPT,
        created_at=now,
        updated_at=now,
        **_default_configuration().model_dump(),
    )


def update_assignment_rule(
    db: Session,
    project_id: UUID,
    data: VisitorAssignmentRuleUpdate,
) -> VisitorAssignmentRuleResponse:
    """Save requested fields without changing an existing enablement choice."""
    if (
        "llm_assignment_enabled" in data.model_fields_set
        and data.llm_assignment_enabled is None
    ):
        raise HTTPException(422, "llm_assignment_enabled cannot be null")

    if data.ai_provider_id is not None:
        provider = (
            db.query(AIProvider)
            .filter(
                AIProvider.id == data.ai_provider_id,
                AIProvider.project_id == project_id,
                AIProvider.deleted_at.is_(None),
            )
            .first()
        )
        if provider is None:
            raise HTTPException(
                404, "AI provider not found or does not belong to this project"
            )

    rule = (
        db.query(VisitorAssignmentRule)
        .filter(VisitorAssignmentRule.project_id == project_id)
        .first()
    )
    if rule is not None:
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(rule, field, value)
        rule.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        values = _default_configuration().model_dump()
        values.update(data.model_dump(exclude_none=True))
        rule = VisitorAssignmentRule(project_id=project_id, **values)
        db.add(rule)

    db.commit()
    db.refresh(rule)
    logger.info(
        "Visitor assignment rule saved",
        extra={"project_id": str(project_id), "rule_id": str(rule.id)},
    )
    return VisitorAssignmentRuleResponse.model_validate(rule)
