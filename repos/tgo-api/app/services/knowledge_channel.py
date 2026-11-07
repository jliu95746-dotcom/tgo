"""Map platform types to governed knowledge channels."""

from app.models.platform import DIRECT_VISITOR_PLATFORM_TYPES, PlatformType
from app.schemas.knowledge import KnowledgeChannel


def resolve_platform_knowledge_channel(platform_type: str) -> KnowledgeChannel:
    (
        'Return the automatic-answer channel for an external customer '
        'platform.'
    )
    if platform_type == PlatformType.WECOM.value:
        return KnowledgeChannel.WECOM_KF
    if platform_type in DIRECT_VISITOR_PLATFORM_TYPES:
        return KnowledgeChannel.WEB
    if platform_type == PlatformType.PHONE.value:
        return KnowledgeChannel.PHONE
    return KnowledgeChannel.APP
