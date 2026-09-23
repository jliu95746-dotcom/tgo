"""Private HTTP identity shared with knowledge service, never exposed to clients."""

from app.config import settings


def rag_service_headers(base_url: str | None = None) -> dict[str, str]:
    if (
        settings.saas_enabled
        and settings.saas_billing_enabled
        and settings.saas_internal_token
    ):
        if base_url is not None and base_url.rstrip(
            "/"
        ) != settings.rag_service_url.rstrip("/"):
            raise ValueError("SaaS knowledge tools must use the approved RAG endpoint")
        return {"X-SaaS-Service-Token": settings.saas_internal_token.get_secret_value()}
    return {}
