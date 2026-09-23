"""Private IM credentials are independent of browser session rotation."""

import hashlib
import hmac

from app.core.config import settings


def transport_token(uid: str, browser_token: str) -> str:
    if not settings.SAAS_ENABLED:
        return browser_token
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        b"yujian-im-upstream:v1:" + uid.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
