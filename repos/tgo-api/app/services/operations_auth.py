"""Purpose-bound operator sessions, never company-staff authorization."""

from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe
from uuid import UUID

from jose import JWTError, jwt
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.core.security import get_password_hash, verify_password
from app.models.platform_operator import PlatformOperator
from app.schemas.operations import (
    OperatorCreateRequest,
    OperatorLoginResponse,
    OperatorResponse,
)

AUDIENCE = "yujian-operations"
ISSUER = "yujian-platform"
_DUMMY_HASH = get_password_hash(token_urlsafe(24))


def require_operations_enabled() -> None:
    if not settings.OPS_ENABLED:
        raise TGOAPIException("运营入口尚未启用", "NOT_FOUND", status_code=404)


def signing_key() -> str:
    require_operations_enabled()
    secret = settings.OPS_SECRET_KEY
    if secret is None or len(secret.get_secret_value()) < 32:
        raise TGOAPIException(
            "运营登录尚未配置",
            "OPERATIONS_NOT_CONFIGURED",
            status_code=503,
        )
    return secret.get_secret_value()


def unauthorized() -> TGOAPIException:
    return TGOAPIException("运营账号或登录凭据无效", "UNAUTHORIZED", status_code=401)


def authenticate_operator(
    db: Session, email: str, password: str,
) -> PlatformOperator:
    signing_key()
    operator = (
        db.query(PlatformOperator)
        .filter(
            PlatformOperator.email == email.strip().lower(),
            PlatformOperator.deleted_at.is_(None),
        )
        .first()
    )
    password_hash = operator.password_hash if operator else _DUMMY_HASH
    try:
        valid = verify_password(password, password_hash)
    except (ValueError, TypeError):
        valid = False
    if operator is None or not valid or not operator.is_active:
        raise unauthorized()
    operator.last_login_at = datetime.now(timezone.utc)
    db.commit()
    return operator


def issue_operator_token(operator: PlatformOperator) -> OperatorLoginResponse:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.OPS_ACCESS_TOKEN_MINUTES)
    token = jwt.encode(
        {
            "sub": str(operator.id),
            "type": "platform_operator",
            "aud": AUDIENCE,
            "iss": ISSUER,
            "version": operator.token_version,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        },
        signing_key(),
        algorithm="HS256",
    )
    return OperatorLoginResponse(
        access_token=token,
        expires_at=expires_at,
        operator=OperatorResponse.model_validate(operator),
    )


def resolve_operator_token(db: Session, token: str) -> PlatformOperator:
    key = signing_key()
    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=["HS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={
                "require_exp": True,
                "require_iat": True,
                "require_sub": True,
                "require_aud": True,
                "require_iss": True,
            },
        )
        if (
            payload.get("type") != "platform_operator"
            or "project_id" in payload
        ):
            raise unauthorized()
        identifier = UUID(payload["sub"])
        version = payload.get("version")
        if type(version) is not int:
            raise unauthorized()
    except (JWTError, ValueError, TypeError, KeyError) as exc:
        raise unauthorized() from exc
    operator = (
        db.query(PlatformOperator)
        .filter(
            PlatformOperator.id == identifier,
            PlatformOperator.is_active.is_(True),
            PlatformOperator.deleted_at.is_(None),
            PlatformOperator.token_version == version,
        )
        .first()
    )
    if operator is None:
        raise unauthorized()
    return operator


def create_operator(
    db: Session, data: OperatorCreateRequest,
) -> OperatorResponse:
    """Local provisioning only; never promote a company staff record."""
    existing = (
        db.query(PlatformOperator.id)
        .filter(
            func.lower(PlatformOperator.email) == data.email,
        )
        .first()
    )
    if existing:
        raise TGOAPIException("运营账号已存在", "CONFLICT", status_code=409)
    operator = PlatformOperator(
        email=data.email,
        name=data.name,
        password_hash=get_password_hash(data.password.get_secret_value()),
    )
    try:
        db.add(operator)
        db.commit()
        db.refresh(operator)
    except IntegrityError as exc:
        db.rollback()
        raise TGOAPIException("运营账号已存在", "CONFLICT", status_code=409) from exc
    return OperatorResponse.model_validate(operator)
