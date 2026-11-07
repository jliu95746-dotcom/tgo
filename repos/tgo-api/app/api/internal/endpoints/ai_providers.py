"Internal AI Provider endpoints.\n\nThis module provides internal \
endpoints for fetching AI provider configuration,\nincluding decrypted \
API keys. These endpoints are for internal \
service-to-service\ncommunication only (e.g., tgo-vision-agent -> \
tgo-api).\n\nNo authentication required - assumes network-level \
isolation.\n"

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import AIProvider
from app.utils.crypto import decrypt_str

router = APIRouter()


@router.get("/{provider_id}/config")
async def get_provider_config(
    provider_id: UUID,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    (
        'Get AI provider configuration for internal services.\n'
        '    \n'
        '    This endpoint returns the full provider configuration including '
        'the decrypted\n'
        '    API key. It is designed for internal service-to-service '
        'communication.\n'
        '    \n'
        '    Args:\n'
        '        provider_id: UUID of the AI provider\n'
        '        \n'
        '    Returns:\n'
        '        Provider configuration including:\n'
        '        - provider_type: Provider type (openai, anthropic, '
        'dashscope, azure_openai, etc.)\n'
        '        - api_key: Decrypted API key (plaintext)\n'
        '        - api_base_url: Base URL for API calls (optional)\n'
        '        - config: Additional provider-specific configuration\n'
        '        \n'
        '    Raises:\n'
        '        404: Provider not found\n'
        '        400: Provider is not active or has no API key\n'
        '    '
    )
    provider = (
        db.query(AIProvider)
        .filter(
            AIProvider.id == provider_id,
            AIProvider.deleted_at.is_(None),
        )
        .first()
    )

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AI provider not found",
        )

    if not provider.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="AI provider is not active",
        )

    # Decrypt API key
    plain_key = decrypt_str(provider.api_key) if provider.api_key else None
    if not plain_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="AI provider has no API key configured",
        )

    return {
        "provider_type": provider.provider,
        "api_key": plain_key,
        "api_base_url": provider.api_base_url,
        "config": provider.config or {},
    }
