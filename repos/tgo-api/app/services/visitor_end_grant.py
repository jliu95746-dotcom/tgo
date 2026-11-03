"""An end-only grant can survive local access deletion without granting reads."""

from datetime import UTC, datetime, timedelta
import hmac
from uuid import uuid4

from jose import JWTError, jwt
from pydantic import SecretStr, ValidationError

from app.core.config import settings
from app.models.visitor import Visitor
from app.schemas.visitor_access import VisitorEndGrantClaims

END_AUDIENCE = "yujian-visitor-end"
END_LIFETIME = timedelta(days=30)


def issue_visitor_end_token(visitor: Visitor, *, lifetime: timedelta = END_LIFETIME) -> SecretStr:
    if visitor.access_secret_digest is None or visitor.access_retired_at is not None:
        raise ValueError("Only current owned access can issue an end grant")
    now = datetime.now(UTC)
    value = jwt.encode({
        "sub": str(visitor.id), "project_id": str(visitor.project_id),
        "platform_id": str(visitor.platform_id), "type": "visitor_end", "aud": END_AUDIENCE,
        "iat": now, "exp": now + lifetime, "jti": uuid4().hex,
        "access_revision": visitor.access_revision, "holder_digest": visitor.access_secret_digest,
    }, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    if not isinstance(value, str):
        raise TypeError("JWT encoder must return a string")
    return SecretStr(value)


def verifies_original_end(visitor: Visitor, token: SecretStr | None) -> bool:
    if token is None or visitor.access_secret_digest is None:
        return False
    try:
        claims = VisitorEndGrantClaims.model_validate(jwt.decode(
            token.get_secret_value(), settings.SECRET_KEY, algorithms=[settings.ALGORITHM],
            audience=END_AUDIENCE, options={"require_exp": True, "require_sub": True, "require_aud": True},
        ))
    except (JWTError, ValidationError, TypeError, ValueError):
        return False
    original_revision = visitor.access_revision - (1 if visitor.access_retired_at is not None else 0)
    return (
        claims.sub == visitor.id and claims.project_id == visitor.project_id
        and claims.platform_id == visitor.platform_id and claims.access_revision == original_revision
        and hmac.compare_digest(claims.holder_digest, visitor.access_secret_digest)
    )
