"""Recheck the current administrator without loading an obsolete template or model."""
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.schemas.agent_configuration_actor import AgentConfigurationActor, AgentConfigurationActorQuery
from app.services.agent_activation_requirements import require_activation_actor


def current_configuration_actor(db: Session, query: AgentConfigurationActorQuery) -> AgentConfigurationActor:
    require_activation_actor(db, query.project_id, query.requested_by)
    return AgentConfigurationActor(**query.model_dump(), checked_at=datetime.now(UTC))
