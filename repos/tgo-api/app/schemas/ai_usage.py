"""Private quota authorization contracts; never mounted on the public API."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from app.schemas.platform_models import PlatformModelRuntime


class UsageAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    reservation_id: UUID | None = None
    lease_id: UUID | None = None


class UsageAuthorizationResult(BaseModel):
    authorized: bool
    metered: bool
    platform_model: PlatformModelRuntime | None = None
