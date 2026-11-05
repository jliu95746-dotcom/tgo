"""Keep model decisions bound to the configuration used for their request."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import AIProvider, VisitorAssignmentRule
from app.services.staff_assignment_model import (
    AssignmentModel,
    resolve_assignment_model,
)


@dataclass(frozen=True)
class AssignmentConfiguration:
    project_id: UUID
    rule_id: UUID
    rule_version: datetime | None
    rule_model: str | None
    system_prompt: str
    selection: AssignmentModel


def read_assignment_rule(
    db: Session,
    project_id: UUID,
    *,
    rule_id: UUID | None = None,
    lock: bool = False,
) -> VisitorAssignmentRule | None:
    query = db.query(VisitorAssignmentRule).filter(
        VisitorAssignmentRule.project_id == project_id
    )
    if rule_id is not None:
        query = query.filter(VisitorAssignmentRule.id == rule_id)
    if lock:
        query = query.with_for_update(read=True)
    return query.populate_existing().first()


def capture_assignment_configuration(
    db: Session,
    project_id: UUID,
    rule: VisitorAssignmentRule,
) -> AssignmentConfiguration | None:
    """Read current configuration without holding locks across a model call."""
    if rule.project_id != project_id:
        return None
    current = read_assignment_rule(db, project_id, rule_id=rule.id)
    if current is None or not current.llm_assignment_enabled:
        return None
    selection = resolve_assignment_model(db, project_id, current)
    if selection is None:
        return None
    return AssignmentConfiguration(
        project_id=project_id,
        rule_id=current.id,
        rule_version=current.updated_at,
        rule_model=current.model,
        system_prompt=current.effective_prompt,
        selection=selection,
    )


def assignment_configuration_is_current(
    db: Session,
    snapshot: AssignmentConfiguration,
) -> bool:
    """Lock provider then rule only after the external wait has completed."""
    provider = (
        db.query(AIProvider)
        .filter(
            AIProvider.id == snapshot.selection.provider_id,
            AIProvider.project_id == snapshot.project_id,
            AIProvider.is_active.is_(True),
            AIProvider.deleted_at.is_(None),
        )
        .with_for_update(read=True)
        .populate_existing()
        .first()
    )
    if provider is None:
        return False
    current = read_assignment_rule(
        db, snapshot.project_id, rule_id=snapshot.rule_id, lock=True
    )
    if current is None or not current.llm_assignment_enabled:
        return False
    model = (current.model or "").strip() or (
        provider.default_model or ""
    ).strip()
    return (
        current.ai_provider_id == snapshot.selection.provider_id
        and current.updated_at == snapshot.rule_version
        and current.model == snapshot.rule_model
        and current.effective_prompt == snapshot.system_prompt
        and provider.updated_at == snapshot.selection.provider_version
        and model == snapshot.selection.model
    )
