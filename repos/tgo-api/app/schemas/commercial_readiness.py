"""Configuration readiness is distinct from external integration verification."""

from typing import Literal

from pydantic import BaseModel


class ReadinessItem(BaseModel):
    code: Literal[
        "email", "wechat", "refund_callback", "internal_auth", "platform_model"
    ]
    configured: bool


class CommercialReadiness(BaseModel):
    billing_enabled: bool
    purchases_enabled: bool
    registration_enabled: bool
    checks: list[ReadinessItem]
