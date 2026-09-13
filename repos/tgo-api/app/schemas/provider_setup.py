"""Transient model setup inputs. These are never persisted by preview/probe."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field, SecretStr, JsonValue


class ProviderConnectionDraft(BaseModel):
    provider_id: UUID | None = None
    provider: str = Field(min_length=1, max_length=50)
    api_base_url: str = Field(min_length=1, max_length=255)
    api_key: SecretStr | None = None
    config: dict[str, JsonValue] | None = None


class ProviderModelProbe(ProviderConnectionDraft):
    model_id: str = Field(min_length=1, max_length=200)
    model_type: Literal['chat', 'embedding', 'asr', 'ocr', 'vlm'] = 'chat'


class ModelProbeResult(BaseModel):
    success: bool
    message: str
