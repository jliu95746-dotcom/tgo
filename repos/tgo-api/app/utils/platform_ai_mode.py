"""Default reception modes shared by routing and visitor display."""

from app.models.platform import PlatformAIMode, PlatformType


def effective_platform_ai_mode(
    platform_type: str | None, configured_mode: str | None
) -> str | None:
    if platform_type == PlatformType.WECHAT_LOCAL:
        return PlatformAIMode.AUTO.value
    return configured_mode
