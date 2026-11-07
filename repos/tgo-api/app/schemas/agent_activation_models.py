"""Private model-owner attestation; no runtime credentials enter a receipt."""
import hashlib
import hmac
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class AgentActivationModelQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    requested_by: UUID
    model: str = Field(min_length=1, max_length=150)
    provider_id: UUID | None = None


class ActivationModelRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=150)
    provider_kind: str = Field(min_length=1, max_length=40)
    api_key: SecretStr
    vendor: str | None = None
    api_base_url: str | None = None
    organization: str | None = None
    timeout: float | None = None

    def private_revision(self, key: str) -> str:
        payload = self.model_dump(mode="json", exclude={"api_key"})
        payload["api_key"] = self.api_key.get_secret_value()
        raw = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hmac.new(key.encode(), raw.encode(), hashlib.sha256).hexdigest()


class AgentActivationModel(AgentActivationModelQuery):
    scope: Literal["model_configuration"] = "model_configuration"
    can_enable: Literal[False] = False
    source: Literal["tenant", "platform"] = "tenant"
    status: Literal[
        "ready", "missing", "disabled", "not_synced", "unavailable"
    ]
    effective_model: str | None = None
    runtime_revision: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    owner_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def ready_requires_revisions(self) -> "AgentActivationModel":
        if self.status == "ready" and not (
            self.effective_model
            and self.runtime_revision
            and self.owner_revision
        ):
            raise ValueError("Missing model owner revision")
        if self.status != "ready" and (
            self.runtime_revision or self.owner_revision
        ):
            raise ValueError("Unready model cannot provide proof")
        return self
