"""Short-lived, one-use email proof for signup before an account exists."""

from hashlib import sha256
from hmac import compare_digest, new as hmac_new
from secrets import randbelow

from fastapi import HTTPException
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.models import Staff, SystemSetup
from app.services.company_email import MailPayload, require_mail_configuration
from app.services.company_mail_delivery import send_mail
from app.services.platform_models import runtime_model
from app.services.registration_limit import limit_registration

CODE_LIFETIME_SECONDS = 900
GENERIC_CODE_ERROR = "验证码无效或已过期"
CONSUME_CODE = """
local actual = redis.call('GET', KEYS[1])
if actual and actual == ARGV[1] then
  redis.call('DEL', KEYS[1])
  return 1
end
return 0
"""


def challenge_key(email: str) -> str:
    digest = sha256(email.strip().lower().encode("utf-8")).hexdigest()
    return f"registration:email-code:{digest}"


def challenge_hash(email: str, code: str) -> str:
    message = f"registration:{email.strip().lower()}:{code}".encode("utf-8")
    return hmac_new(
        settings.SECRET_KEY.encode("utf-8"), message, sha256
    ).hexdigest()


async def send_registration_code(db: Session, email: str) -> None:
    """Send only for a new address; keep the public response generic."""
    if not (
        settings.SAAS_REGISTRATION_ENABLED and settings.SAAS_BILLING_ENABLED
    ):
        raise HTTPException(403, "企业自助注册尚未开放")
    require_mail_configuration()
    setup = db.scalar(
        select(SystemSetup).where(SystemSetup.is_installed.is_(True))
    )
    if setup is None or runtime_model(db) is None:
        raise HTTPException(503, "企业注册尚未配置完成")
    normalized = email.strip().lower()
    await limit_registration(f"registration-email:{normalized}")
    existing = db.scalar(
        select(Staff.id).where(func.lower(Staff.username) == normalized)
    )
    if existing is not None:
        return
    if not settings.REDIS_URL:
        raise HTTPException(503, "Registration is temporarily unavailable")
    code = f"{randbelow(1_000_000):06d}"
    key = challenge_key(normalized)
    digest = challenge_hash(normalized, code)
    try:
        async with Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2
        ) as client:
            await client.setex(key, CODE_LIFETIME_SECONDS, digest)
        await run_in_threadpool(
            send_mail,
            MailPayload(
                recipient=normalized,
                subject="域见注册验证码",
                body=(
                    f"您的注册验证码是：{code}\n\n"
                    "验证码 15 分钟内有效，请勿告知他人。\n"
                    "如非本人申请，请忽略此邮件。"
                ),
            ),
        )
    except RedisError as exc:
        raise HTTPException(
            503, "Registration is temporarily unavailable"
        ) from exc
    except Exception as exc:
        try:
            async with Redis.from_url(settings.REDIS_URL) as client:
                await client.delete(key)
        except RedisError:
            pass
        raise HTTPException(503, "验证码发送失败，请稍后重试") from exc


async def consume_registration_code(email: str, code: str) -> None:
    if not settings.REDIS_URL:
        raise HTTPException(503, "Registration is temporarily unavailable")
    await limit_registration(f"registration-code:{email.strip().lower()}")
    try:
        async with Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2
        ) as client:
            consumed = await client.eval(
                CONSUME_CODE,
                1,
                challenge_key(email),
                challenge_hash(email, code),
            )
    except RedisError as exc:
        raise HTTPException(
            503, "Registration is temporarily unavailable"
        ) from exc
    if not compare_digest(str(consumed), "1"):
        raise HTTPException(400, GENERIC_CODE_ERROR)
