"""Identity and channel checks for the SaaS realtime message gateway."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.core.security import resolve_staff_token
from app.models import Platform, Project, Staff, Visitor
from app.services.channel_access import require_staff_channel_access
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import build_visitor_channel_id

IMActor = Staff | Visitor
VISITOR_IM_AUDIENCE = "yujian-visitor-im"


def _unauthorized() -> TGOAPIException:
    return TGOAPIException(
        "实时消息凭据无效或已过期",
        "UNAUTHORIZED",
        status_code=401,
    )


def issue_visitor_im_token(visitor: Visitor, lifetime: timedelta) -> str:
    """Issue a visitor-only credential, distinct from staff and file tokens."""
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": str(visitor.id),
            "project_id": str(visitor.project_id),
            "platform_id": str(visitor.platform_id),
            "type": "visitor_im",
            "aud": VISITOR_IM_AUDIENCE,
            "iat": now,
            "exp": now + lifetime,
            "jti": uuid4().hex,
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    if not isinstance(token, str):
        raise TypeError("JWT encoder must return a string")
    return token


def authenticate_im_actor(db: Session, uid: str, token: str) -> IMActor:
    """Resolve current membership; call again before each relayed operation."""
    if uid.endswith("-staff"):
        staff = resolve_staff_token(db, token)
        if staff is None or uid != f"{staff.id}-staff":
            raise _unauthorized()
        return staff

    if not uid.endswith("-vtr"):
        raise _unauthorized()
    try:
        claims = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            audience=VISITOR_IM_AUDIENCE,
            options={
                "require_exp": True,
                "require_sub": True,
                "require_aud": True,
            },
        )
        if claims.get("type") != "visitor_im":
            raise _unauthorized()
        visitor_id = UUID(claims["sub"])
        project_id = UUID(claims["project_id"])
        platform_id = UUID(claims["platform_id"])
        if uid != build_visitor_channel_id(visitor_id):
            raise _unauthorized()
    except (JWTError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise _unauthorized() from exc

    visitor = (
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
            Platform.project_id == project_id,
            Platform.is_active.is_(True),
            Platform.deleted_at.is_(None),
            Project.deleted_at.is_(None),
        )
        .first()
    )
    if visitor is None:
        raise _unauthorized()
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
