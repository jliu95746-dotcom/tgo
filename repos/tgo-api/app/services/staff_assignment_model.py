"""Resolve the dispatch model from the current project's active provider."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import AIProvider, VisitorAssignmentRule


@dataclass(frozen=True)
class AssignmentModel:
    provider_id: UUID
    model: str
    provider_version: datetime | None = None


def resolve_assignment_model(
    db: Session,
    project_id: UUID,
    rule: VisitorAssignmentRule,
) -> AssignmentModel | None:
    """Respect an explicit model, otherwise use the fresh provider default."""
    if rule.project_id != project_id or rule.ai_provider_id is None:
        return None
    provider = (
        db.query(AIProvider)
        .filter(
            AIProvider.id == rule.ai_provider_id,
            AIProvider.project_id == project_id,
            AIProvider.is_active.is_(True),
            AIProvider.deleted_at.is_(None),
        )
        .populate_existing()
        .first()
    )
    if provider is None:
        return None
    model = (rule.model or "").strip() or (
        provider.default_model or ""
    ).strip()
    if not model:
        return None
    return AssignmentModel(
        provider_id=provider.id,
        model=model,
        provider_version=provider.updated_at,
    )
