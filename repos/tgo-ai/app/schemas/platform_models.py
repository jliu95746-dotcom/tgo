"""Trusted platform credentials are transient and never returned by tenant APIs."""

from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, SecretStr


class PlatformModelRuntime(BaseModel):
    model: str
    provider_kind: Literal["openai", "openai_compatible", "anthropic", "google"]
    api_base_url: str | None = None
    vendor: str | None = None
    active: bool = True
    api_key: SecretStr
    input_fen_per_million: Decimal | None = None
    output_fen_per_million: Decimal | None = None
