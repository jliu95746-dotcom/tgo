"""Identity and channel checks for the SaaS realtime message gateway."""

from datetime import datetime, timedelta, timezone
from typing import TypeAlias
from uuid import uuid4

from jose import JWTError, jwt
from pydantic import ValidationError
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.core.security import resolve_staff_token
from app.models import Platform, Project, Staff, Visitor
from app.models.platform import PlatformType
from app.schemas.visitor_access import VisitorBusinessAccess, VisitorIMAccessClaims
from app.services.channel_access import require_staff_channel_access
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import build_visitor_channel_id

IMActor: TypeAlias = Staff | Visitor
VISITOR_IM_AUDIENCE = "yujian-visitor-im"


def _unauthorized() -> TGOAPIException:
    return TGOAPIException(
        "实时消息凭据无效或已过期",
        "UNAUTHORIZED",
        status_code=401,
    )


def issue_visitor_im_token(
    visitor: Visitor,
    lifetime: timedelta,
    *,
    business: VisitorBusinessAccess | None = None,
) -> str:
    """Issue a visitor-only credential, distinct from staff and file tokens."""
    now = datetime.now(timezone.utc)
    expires_at = now + lifetime
    if business is not None and business.references:
        if business.expires_at is None or business.expires_at <= now:
            raise _unauthorized()
        expires_at = min(expires_at, business.expires_at)
    token = jwt.encode(
        {
            "sub": str(visitor.id),
            "project_id": str(visitor.project_id),
            "platform_id": str(visitor.platform_id),
            "type": "visitor_im",
            "aud": VISITOR_IM_AUDIENCE,
            "iat": now,
            "exp": expires_at,
            "access_revision": visitor.access_revision,
            "business_references": [
                item.model_dump(mode="json") for item in business.references
            ]
            if business
            else [],
            "jti": uuid4().hex,
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    if not isinstance(token, str):
        raise TypeError("JWT encoder must return a string")
    return token


def read_visitor_im_access(token: str) -> VisitorIMAccessClaims:
    """Read signed visitor claims; callers must also check current database scope."""
    try:
        claims = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            audience=VISITOR_IM_AUDIENCE,
            options={"require_exp": True, "require_sub": True, "require_aud": True},
        )
        return VisitorIMAccessClaims.model_validate(claims)
    except (JWTError, ValidationError, ValueError, TypeError):
        raise _unauthorized() from None


def authenticate_im_actor(db: Session, uid: str, token: str) -> IMActor:
    """Resolve current membership; call again before each relayed operation."""
    if uid.endswith("-staff"):
        staff: Staff | None = resolve_staff_token(db, token)
        if staff is None or uid != f"{staff.id}-staff":
            raise _unauthorized()
        return staff

    if not uid.endswith("-vtr"):
        raise _unauthorized()
    claims = read_visitor_im_access(token)
    return authenticate_visitor_im_claims(db, uid, claims)


def authenticate_visitor_im_claims(
    db: Session, uid: str, claims: VisitorIMAccessClaims,
) -> Visitor:
    """Recheck already verified claims, including their original expiry and owner."""
    if claims.exp <= int(datetime.now(timezone.utc).timestamp()):
        raise _unauthorized()
    visitor_id, project_id, platform_id = claims.sub, claims.project_id, claims.platform_id
    if uid != build_visitor_channel_id(visitor_id):
        raise _unauthorized()

    visitor: Visitor | None = (
        db.query(Visitor)
        .join(
            Platform,
            Visitor.platform_id == Platform.id,
        )
        .join(Project, Visitor.project_id == Project.id)
        .filter(
            Visitor.id == visitor_id,
            Visitor.project_id == project_id,
            Visitor.platform_id == platform_id,
            Visitor.deleted_at.is_(None),
            Visitor.access_retired_at.is_(None),
            Platform.project_id == project_id,
            or_(
                Platform.type == "website",
                and_(
                    Platform.type == PlatformType.WECHAT_LOCAL.value,
                    Visitor.platform_open_id.regexp_match(r"^wechat-local:[a-f0-9]{16}:filehelper$"),
                ),
            ),
            Platform.is_active.is_(True),
            Platform.deleted_at.is_(None),
            Project.deleted_at.is_(None),
        )
        .populate_existing()
        .first()
    )
    if visitor is None:
        raise _unauthorized()
    if claims.access_revision != visitor.access_revision and (
        claims.access_revision is not None or visitor.access_secret_digest is not None
    ):
        raise _unauthorized()
    # The JWT is only a reference: check its original customer binding every operation.
    from app.services.visitor_access import check_im_business_references

    check_im_business_references(db, visitor, claims.business_references)
    return visitor


async def authorize_im_channel(
    db: Session,
    actor: IMActor,
    channel_id: str,
    channel_type: int,
) -> None:
    """Check the same ownership for outbound sends and inbound deliveries."""
    if isinstance(actor, Staff):
        await require_staff_channel_access(db, actor, channel_id, channel_type)
        return
    if (
        channel_type != CHANNEL_TYPE_CUSTOMER_SERVICE
        or channel_id != build_visitor_channel_id(actor.id)
    ):
        raise TGOAPIException(
            "无权访问该实时会话",
            "FORBIDDEN",
            status_code=403,
        )
