"""API key authentication for project-scoped access."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.exceptions import AuthenticationError
from app.models.project import Project


async def get_project_from_api_key(api_key: str, db: AsyncSession) -> Project:
    """
    Get project from API key.

    Args:
        api_key: API key string
        db: Database session

    Returns:
        Project associated with the API key

    Raises:
        AuthenticationError: If API key is invalid or project not found
    """
    # Handle development API key
    if api_key == "dev":
        if not settings.is_development:
            raise AuthenticationError(
                "Development API key is only valid in development environment"
            )

        # Attempt to locate development project in the database
        try:
            stmt = select(Project).where(Project.api_key == "dev")
            result = await db.execute(stmt)
            project = result.scalar_one_or_none()
        except OperationalError:
            # In some test/client environments the tables may not be
            # initialized yet.
            # For development ergonomics, allow a synthetic project without
            # hitting the DB.
            project = None
            table_missing = True
        else:
            table_missing = False

        if project and not project.is_deleted:
            return project

        if table_missing:
            # Fallback: synthesize an in-memory project to support dev
            # workflows without seed data
            return Project(
                id=uuid.uuid4(),
                name="Development Project",
                api_key="dev",
                synced_at=datetime.utcnow(),
            )

        # If the table exists but the dev project was not found, raise for
        # clarity
        raise AuthenticationError("Development project not found")

    # Validate standard API key format
    if not api_key.startswith(settings.api_key_prefix):
        raise AuthenticationError("Invalid API key format")

    # Look up project by API key
    stmt = select(Project).where(Project.api_key == api_key)
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()

    if not project:
        raise AuthenticationError("Invalid API key")

    if project.is_deleted:
        raise AuthenticationError("Project is deleted")

    return project
