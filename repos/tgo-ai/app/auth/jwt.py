"""JWT token handling for service-to-service authentication."""

import uuid
from datetime import datetime, timedelta
from typing import Dict, Optional

from jose import jwt
from jose.exceptions import JOSEError
from pydantic import JsonValue, TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import AuthenticationError
from app.models.project import Project

_jwt_claims = TypeAdapter(dict[str, JsonValue])


def create_access_token(
    data: Dict[str, str], expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Create a JWT access token.

    Args:
        data: Data to encode in the token
        expires_delta: Token expiration time

    Returns:
        Encoded JWT token
    """
    to_encode: dict[str, str | datetime] = dict(data)
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            minutes=settings.access_token_expire_minutes,
        )

    to_encode.update({"exp": expire})
    try:
        encoded_jwt = jwt.encode(
            to_encode, settings.secret_key, algorithm=settings.algorithm,
        )
    except (JOSEError, TypeError, ValueError):
        raise AuthenticationError("Unable to create access token") from None
    if not isinstance(encoded_jwt, str) or not encoded_jwt:
        raise AuthenticationError("Unable to create access token")
    return encoded_jwt


def verify_token(token: str) -> dict[str, JsonValue]:
    """
    Verify and decode a JWT token.

    Args:
        token: JWT token to verify

    Returns:
        Decoded token payload

    Raises:
        AuthenticationError: If token is invalid
    """
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.algorithm],
        )
        return _jwt_claims.validate_python(payload, strict=True)
    except (JOSEError, TypeError, ValueError):
        raise AuthenticationError("Invalid token") from None


async def get_project_from_jwt(token: str, db: AsyncSession) -> Project:
    """
    Get project from JWT token.

    Args:
        token: JWT token
        db: Database session

    Returns:
        Project associated with the token

    Raises:
        AuthenticationError: If token is invalid or project not found
    """
    payload = verify_token(token)

    # Extract project_id from token payload
    project_id_str = payload.get("project_id")
    if not project_id_str:
        raise AuthenticationError("Token missing project_id claim")
    if not isinstance(project_id_str, str):
        raise AuthenticationError("Invalid project_id format in token")

    try:
        project_id = uuid.UUID(project_id_str)
    except ValueError:
        raise AuthenticationError(
            "Invalid project_id format in token",
        ) from None

    # Look up project in database
    project = await db.get(Project, project_id)
    if not project:
        raise AuthenticationError("Project not found")

    if project.is_deleted:
        raise AuthenticationError("Project is deleted")

    return project
