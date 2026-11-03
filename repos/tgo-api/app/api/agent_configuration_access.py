"""Fresh login-owned administrator identity for employee configuration writes."""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_authenticated_project, require_admin
from app.models.project import Project
from app.models.staff import Staff
from app.services.agent_activation_requirements import require_activation_actor


def require_agent_configuration_actor(
    project_and_key: tuple[Project, str] = Depends(get_authenticated_project),
    actor: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> Staff:
    require_activation_actor(db, project_and_key[0].id, actor.id)
    return actor
