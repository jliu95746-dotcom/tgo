"""Issue short-lived, project-scoped access to the internal device service."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from jose import jwt

from app.core.config import settings


def device_service_headers(project_id: str) -> dict[str, str]:
    project = str(UUID(project_id))
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": "tgo-api", "iss": "tgo-internal", "aud": "tgo-device-control",
         "project_id": project, "iat": now, "exp": now + timedelta(minutes=5),
         "jti": uuid4().hex},
        settings.SECRET_KEY, algorithm="HS256",
    )
    return {"Authorization": "Bearer " + token}
