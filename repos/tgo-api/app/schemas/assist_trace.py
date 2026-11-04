"""Model-call evidence retained with a staff-visible assist draft."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

AssistTracePhase = Literal["fact_answer", "expression_rewrite", "fact_audit"]


class AssistRunTrace(BaseModel):
    """Configured runtime identity; no customer text or prompt body."""

    model_config = ConfigDict(extra="forbid")

    phase: AssistTracePhase
    model_name: str | None = Field(default=None, max_length=200)
    configured_prompt_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    api_template_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    requested_agent_id: UUID | None = None
    request_system_prompt_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
