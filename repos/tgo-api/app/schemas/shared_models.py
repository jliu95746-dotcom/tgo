"""Operator-owned model catalogue; secrets never enter tenant responses."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr

ModelPurpose = Literal["chat", "embedding", "asr", "ocr", "vlm"]


class SharedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(min_length=1, max_length=100)
    model_type: ModelPurpose
    capabilities: dict[str, bool] | None = None


class SharedSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: UUID
    model_id: str = Field(min_length=1, max_length=100)


class SharedProviderDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    provider: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    api_base_url: str | None = Field(default=None, max_length=255)
    config: dict[str, JsonValue] | None = None
    default_model: str | None = Field(default=None, max_length=100)
    is_active: bool = True
    models: list[SharedModel] = Field(min_length=1, max_length=50)


class SharedProviderChange(SharedProviderDefinition):
    api_key: SecretStr | None = Field(default=None, max_length=255)


class StoredSharedProvider(SharedProviderDefinition):
    encrypted_api_key: str


class SharedProviderView(SharedProviderDefinition):
    has_api_key: bool


class StoredSharedModels(BaseModel):
    version: int
    providers: list[StoredSharedProvider]
    defaults: dict[ModelPurpose, SharedSelection]


class SharedModelsView(BaseModel):
    version: int
    enabled: bool
    synchronization: Literal["inactive", "pending", "failed", "synced"]
    providers: list[SharedProviderView]
    defaults: dict[ModelPurpose, SharedSelection]


class SharedModelsChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    providers: list[SharedProviderChange] = Field(min_length=1)
    defaults: dict[ModelPurpose, SharedSelection]
    reason: str = Field(min_length=5, max_length=500)


class SharedModelsImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_project_id: UUID
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=5, max_length=500)


class SharedConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: UUID
    expected_version: int = Field(ge=1)


class SharedConnectionResult(BaseModel):
    provider_id: UUID
    version: int
    success: bool
    http_status: int | None
    message: str
    checked_at: datetime
