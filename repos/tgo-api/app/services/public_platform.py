"""Construct the visitor channel view using only approved public fields."""

from app.models import Platform
from app.core.config import settings
from app.schemas.platform_schema import PlatformListItemResponse
from app.schemas.public_platform import (
    PublicPlatformResponse,
    PublicWidgetConfig,
)


def public_platform_response(
    platform: Platform,
    presentation: PlatformListItemResponse,
) -> PublicPlatformResponse:
    logo_url = None
    if platform.logo_path:
        base = settings.API_BASE_URL.rstrip("/")
        version = settings.API_V1_STR.rstrip("/")
        logo_url = f"{base}{version}/platforms/{platform.id}/logo"
    return PublicPlatformResponse(
        id=platform.id,
        name=presentation.name or presentation.display_name,
        display_name=presentation.display_name,
        type=presentation.type,
        is_active=platform.is_active,
        logo_url=logo_url,
        config=PublicWidgetConfig.model_validate(
            (platform.config or {}) if platform.type == "website" else {}
        ),
    )
