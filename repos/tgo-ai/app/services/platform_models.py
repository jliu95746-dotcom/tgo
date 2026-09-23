"""Materialize request-scoped platform credentials without storing tenant copies."""

from uuid import UUID
from app.models.llm_provider import LLMProvider
from app.runtime.tools.models import LLMProviderCredentials
from app.schemas.platform_models import PlatformModelRuntime


def current_model() -> PlatformModelRuntime | None:
    from app.services.quota_authorization import (
        current_platform_model,
        metered_execution,
    )

    return current_platform_model.get() if metered_execution.get() else None


def credentials(model: PlatformModelRuntime) -> LLMProviderCredentials:
    return LLMProviderCredentials(
        provider_kind=model.provider_kind,
        vendor=model.vendor,
        api_base_url=model.api_base_url,
        api_key=model.api_key.get_secret_value(),
        timeout=60,
    )


def provider_for_request(identifier: UUID, project_id: UUID) -> LLMProvider | None:
    model = current_model()
    if model is None:
        return None
    return LLMProvider(
        id=identifier,
        project_id=project_id,
        alias="域见平台模型",
        provider_kind=model.provider_kind,
        vendor=model.vendor,
        api_base_url=model.api_base_url,
        api_key=model.api_key.get_secret_value(),
        is_active=True,
    )
