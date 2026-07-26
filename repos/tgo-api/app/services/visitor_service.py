"""Visitor related service logic."""

import hashlib
import re
import uuid
from datetime import datetime
from typing import Optional, List, Tuple

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import (
    Platform,
    Visitor,
    VisitorSystemInfo,
    ChannelMember,
)
from app.schemas.visitor import VisitorServiceModeUpdate, VisitorSystemInfoRequest
from app.services.humanization_service import get_humanization_skill_prompt
from app.services.wukongim_client import wukongim_client
from app.services.geoip_service import geoip_service
from app.utils.const import (
    CHANNEL_TYPE_CUSTOMER_SERVICE,
    MEMBER_TYPE_VISITOR,
)
from app.utils.encoding import build_visitor_channel_id

logger = get_logger("services.visitor")

# Predefined array of realistic visitor names for default visitor generation
DEFAULT_VISITOR_NAMES = [
    "Alex Chen", "Sarah Johnson", "Michael Zhang", "Emma Wilson", "David Kumar",
    "Jessica Martinez", "Ryan O'Connor", "Sophia Lee", "James Anderson", "Olivia Brown",
    "Daniel Garcia", "Isabella Rodriguez", "Matthew Taylor", "Ava Thompson",
    "Christopher White", "Mia Harris", "Andrew Clark", "Emily Lewis", "Joshua Walker",
    "Charlotte Hall", "Kevin Young", "Amelia Allen", "Brandon King", "Harper Wright",
    "Tyler Scott", "Evelyn Green", "Justin Adams", "Abigail Baker", "Nathan Nelson",
    "Ella Carter",
]

# Chinese nickname components
CUSTOMER_SERVICE_ADJECTIVES_ZH = [
    "星光", "温暖", "清晨", "晴空", "暖阳", "微风", "云端", "静谧", "灵动", "璀璨", "悠然", "暮色",
]

CUSTOMER_SERVICE_NOUNS_ZH = [
    "海豚", "星猫", "向日葵", "松果", "雨燕", "晨露", "珊瑚", "雪狐", "轻舟", "薰衣草", "流萤", "橄榄树",
]

# English nickname components
CUSTOMER_SERVICE_ADJECTIVES_EN = [
    "Starry", "Warm", "Morning", "Sunny", "Bright", "Breezy", "Cloud", "Quiet", "Swift", "Shiny", "Calm", "Twilight",
]

CUSTOMER_SERVICE_NOUNS_EN = [
    "Dolphin", "Cat", "Sunflower", "Pine", "Swallow", "Dew", "Coral", "Fox", "Boat", "Lavender", "Firefly", "Olive",
]

# Allowed image types for avatar upload
AVATAR_ALLOWED_MIME_TYPES = {
    "image/jpeg", "image/jpg", "image/png", "image/gif", "image/webp",
}
AVATAR_ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}
AVATAR_MAX_SIZE_MB = 5  # 5MB limit for avatar


async def configure_service_mode(
    visitor: Visitor,
    project_id: str,
    data: VisitorServiceModeUpdate,
) -> None:
    """Apply a three-state service mode and validate its selected skill."""
    skill_name = data.humanization_skill_name
    humanization_enabled = (
        data.service_mode.value != "manual" and data.humanization_skill_enabled
    )
    if humanization_enabled and not skill_name:
        raise ValueError("A humanization skill must be selected before enabling it")
    if skill_name:
        await get_humanization_skill_prompt(project_id, skill_name)

    visitor.service_mode = data.service_mode.value
    visitor.ai_disabled = data.service_mode.value != "auto"
    visitor.humanization_skill_name = skill_name
    visitor.humanization_skill_enabled = humanization_enabled
    visitor.updated_at = datetime.utcnow()


def generate_default_visitor_name(visitor_id: str) -> str:
    """Generate a deterministic visitor name based on the visitor_id."""
    id_bytes = visitor_id.encode('utf-8')
    hash_digest = hashlib.sha256(id_bytes).digest()
    index = int.from_bytes(hash_digest[:4], byteorder='big') % len(DEFAULT_VISITOR_NAMES)
    return DEFAULT_VISITOR_NAMES[index]


def generate_customer_service_nickname(identifier: Optional[str]) -> Tuple[str, str]:
    """Generate friendly fallback nicknames for visitor-facing scenarios."""
    base_identifier = (identifier or "").strip()
    if not base_identifier:
        base_identifier = datetime.utcnow().isoformat()

    digest = hashlib.sha256(base_identifier.encode("utf-8")).digest()

    # English nickname
    adjective_en = CUSTOMER_SERVICE_ADJECTIVES_EN[digest[0] % len(CUSTOMER_SERVICE_ADJECTIVES_EN)]
    noun_en = CUSTOMER_SERVICE_NOUNS_EN[digest[1] % len(CUSTOMER_SERVICE_NOUNS_EN)]

    # Chinese nickname
    adjective_zh = CUSTOMER_SERVICE_ADJECTIVES_ZH[digest[0] % len(CUSTOMER_SERVICE_ADJECTIVES_ZH)]
    noun_zh = CUSTOMER_SERVICE_NOUNS_ZH[digest[1] % len(CUSTOMER_SERVICE_NOUNS_ZH)]

    suffix_value = int.from_bytes(digest[2:4], byteorder="big")
    suffix = format(suffix_value, "x").upper()[:3]

    nickname_en = f"{adjective_en}{noun_en}{suffix}"
    nickname_zh = f"{adjective_zh}{noun_zh}{suffix}"

    return nickname_en, nickname_zh


def resolve_visitor_nickname(
    provided_nickname: Optional[str],
    provided_nickname_zh: Optional[str],
    identifier: Optional[str],
) -> Tuple[str, str]:
    """Return cleaned nicknames or generate defaults when blank."""
    nickname_en = (provided_nickname or "").strip()
    nickname_zh = (provided_nickname_zh or "").strip()

    if nickname_en and nickname_zh:
        return nickname_en, nickname_zh

    generated_en, generated_zh = generate_customer_service_nickname(identifier)
    return nickname_en or generated_en, nickname_zh or generated_zh


async def ensure_visitor_channel(
    db: Session,
    visitor: Visitor,
    platform: Platform,
) -> None:
    """Ensure WuKongIM channel exists for a visitor.
    
    This function separates DB operations from external API calls to minimize
    transaction duration and prevent deadlocks.
    """
    channel_id = build_visitor_channel_id(visitor.id)
    subscribers = [str(visitor.id)+"-vtr"]
    need_create_member = False

    # Phase 1: DB operations in a short transaction
    try:
        existing_visitor_member = (
            db.query(ChannelMember)
            .filter(
                ChannelMember.project_id == platform.project_id,
                ChannelMember.channel_id == channel_id,
                ChannelMember.member_id == visitor.id,
                ChannelMember.deleted_at.is_(None),
            )
            .first()
        )

        if not existing_visitor_member:
            visitor_member = ChannelMember(
                project_id=platform.project_id,
                channel_id=channel_id,
                channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
                member_id=visitor.id,
                member_type=MEMBER_TYPE_VISITOR,
            )
            db.add(visitor_member)
            db.commit()
            need_create_member = True

    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        logger.error(f"Failed to create ChannelMember for visitor: {e}")
        raise

    # Phase 2: External API call (outside transaction)
    try:
        await wukongim_client.create_channel(
            channel_id=channel_id,
            channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
            subscribers=subscribers,
        )

        logger.info(
            "WuKongIM channel ensured for visitor",
            extra={
                "channel_id": channel_id,
                "channel_type": CHANNEL_TYPE_CUSTOMER_SERVICE,
                "visitor_id": str(visitor.id),
                "member_created": need_create_member,
            },
        )
    except Exception as e:
        logger.error(f"Failed to create WuKongIM channel for visitor: {e}")
        # Note: We don't rollback DB changes here as the ChannelMember record
        # is still valid even if WuKongIM sync fails - it can be retried later


async def create_visitor_with_channel(
    db: Session,
    platform: Platform,
    platform_open_id: Optional[str] = None,
    name: Optional[str] = None,
    nickname: Optional[str] = None,
    nickname_zh: Optional[str] = None,
    avatar_url: Optional[str] = None,
    phone_number: Optional[str] = None,
    email: Optional[str] = None,
    company: Optional[str] = None,
    job_title: Optional[str] = None,
    source: Optional[str] = None,
    note: Optional[str] = None,
    custom_attributes: Optional[dict] = None,
    timezone: Optional[str] = None,
    language: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> Visitor:
    """Create a new visitor with WuKongIM channel setup."""
    use_visitor_id_as_open_id = not platform_open_id
    
    if use_visitor_id_as_open_id:
        initial_platform_open_id = f"pending-{uuid.uuid4().hex}"
    else:
        initial_platform_open_id = platform_open_id
    
    resolved_nickname, resolved_nickname_zh = resolve_visitor_nickname(
        nickname, nickname_zh, platform_open_id or None
    )
    
    geo_location = geoip_service.lookup(ip_address)

    visitor = Visitor(
        project_id=platform.project_id,
        platform_id=platform.id,
        platform_open_id=initial_platform_open_id,
        name=name,
        nickname=resolved_nickname,
        nickname_zh=resolved_nickname_zh,
        avatar_url=avatar_url,
        phone_number=phone_number,
        email=email,
        company=company,
        job_title=job_title,
        source=source,
        note=note,
        custom_attributes=custom_attributes or {},
        timezone=timezone,
        language=language,
        ip_address=ip_address,
        geo_country=geo_location.country,
        geo_country_code=geo_location.country_code,
        geo_region=geo_location.region,
        geo_city=geo_location.city,
        geo_isp=geo_location.isp,
        first_visit_time=datetime.utcnow(),
        last_visit_time=datetime.utcnow(),
    )
    db.add(visitor)
    
    if use_visitor_id_as_open_id:
        db.flush()
        visitor.platform_open_id = str(visitor.id) + "-vtr"
    
    db.commit()
    db.refresh(visitor)

    await ensure_visitor_channel(db, visitor, platform)
    return visitor


def upsert_visitor_system_info(
    db: Session,
    visitor: Visitor,
    platform: Platform,
    system_info_payload: Optional[VisitorSystemInfoRequest],
) -> bool:
    """Create or update the visitor's system info record."""
    system_info = visitor.system_info
    created = False
    if not system_info:
        system_info = VisitorSystemInfo(
            project_id=platform.project_id,
            visitor_id=visitor.id,
        )
        db.add(system_info)
        visitor.system_info = system_info
        created = True

    changed = created

    if system_info.platform != platform.name:
        system_info.platform = platform.name
        changed = True

    if system_info.first_seen_at is None:
        system_info.first_seen_at = datetime.utcnow()
        changed = True

    info_data = system_info_payload.model_dump(exclude_none=True) if system_info_payload else {}
    for field in ("source_detail", "browser", "operating_system"):
        if field in info_data and getattr(system_info, field) != info_data[field]:
            setattr(system_info, field, info_data[field])
            changed = True

    return changed


def sanitize_avatar_filename(name: str, limit: int = 100) -> str:
    """Sanitize filename for avatar upload."""
    name = name.replace("\\", "_").replace("/", "_").replace("..", ".")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    if len(name) <= limit:
        return name
    if "." in name:
        base, ext = name.rsplit(".", 1)
        base = base[: max(1, limit - len(ext) - 1)]
        return f"{base}.{ext}"
    return name[:limit]
