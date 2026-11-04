"""Issue short-lived, project-scoped access to the internal device service."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from jose import jwt

from app.core.config import settings


def device_service_headers(
    project_id: str, *, device_id: UUID | None = None
) -> dict[str, str]:
    project = str(UUID(project_id))
    now = datetime.now(timezone.utc)
    claims: dict[str, str | datetime] = {
        "sub": "tgo-api",
        "iss": "tgo-internal",
        "aud": "tgo-device-control",
        "project_id": project,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "jti": uuid4().hex,
    }
    if device_id is not None:
        claims["device_id"] = str(device_id)
    token = jwt.encode(
        claims,
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    return {"Authorization": "Bearer " + token}
