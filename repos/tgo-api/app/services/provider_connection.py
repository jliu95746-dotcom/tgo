"""Shared provider protocol for existing model listing and connection checks."""

from typing import Optional
from fastapi import HTTPException, status
from app.models import AIProvider


def _normalize_base(base: Optional[str]) -> Optional[str]:
    if not base:
        return None
    return base.rstrip("/")


def build_test_request(
    item: AIProvider, plain_key: Optional[str]
) -> tuple[str, str, dict[str, str]]:
    """Return (method, url, headers) for a lightweight connectivity check.
    Raises HTTPException on unsupported provider or missing key.
    """
    if not plain_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="API key is not set for this provider",
        )

    provider = (item.provider or "").lower()
    base = _normalize_base(item.api_base_url)
    headers: dict[str, str] = {}

    if provider in ("openai", "gpt", "gpt-4o", "oai"):
        base = base or "https://api.openai.com/v1"
        url = f"{base}/models"
        headers = {"Authorization": f"Bearer {plain_key}"}
        return ("GET", url, headers)

    if provider in ("anthropic", "claude"):
        base = base or "https://api.anthropic.com"
        url = f"{base}/v1/models"
        version = (item.config or {}).get("anthropic_version") or "2023-06-01"
        headers = {"x-api-key": plain_key, "anthropic-version": str(version)}
        return ("GET", url, headers)

    if provider in ("dashscope", "ali", "aliyun"):
        # Prefer OpenAI-compatible endpoint if base not provided
        if base and "compatible-mode" in base:
            compat = base
        else:
            compat = (base or "https://dashscope.aliyuncs.com") + "/compatible-mode/v1"
        url = f"{compat}/models"
        headers = {"Authorization": f"Bearer {plain_key}"}
        return ("GET", url, headers)

    if provider in ("azure_openai", "azure-openai", "azure"):
        if not base:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="api_base_url is required for Azure OpenAI",
            )
        root = base if "/openai" in base else f"{base}/openai"
        api_version = (item.config or {}).get("api_version") or "2023-12-01-preview"
        url = f"{root}/deployments?api-version={api_version}"
        headers = {"api-key": plain_key}
        return ("GET", url, headers)

    # Fallback: try OpenAI-compatible with provided base
    if base:
        url = f"{base}/models"
        headers = {"Authorization": f"Bearer {plain_key}"}
        return ("GET", url, headers)

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unsupported provider: {item.provider}",
    )
