"""Private, current configuration permissions independent of model/template availability."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AgentConfigurationActorQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    requested_by: UUID


class AgentConfigurationActor(AgentConfigurationActorQuery):
    scope: Literal["agent_configuration_access"] = "agent_configuration_access"
    authorized: Literal[True] = True
    can_enable: Literal[False] = False
    checked_at: datetime
