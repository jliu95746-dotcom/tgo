"Only the visitor's own display information belongs in public widget \
output."

from uuid import UUID
from pydantic import BaseModel, ConfigDict


class VisitorPublicProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str | None = None
    display_nickname: str | None = None
    avatar_url: str | None = None
    is_online: bool
