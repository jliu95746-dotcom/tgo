"Visitor possession is a separate factor from a merchant's customer \
signature."

import hashlib
import hmac
from typing import Protocol
from uuid import UUID

from pydantic import SecretStr
from app.core.config import settings


class VisitorAccessSubject(Protocol):
    id: UUID


def access_digest(visitor: VisitorAccessSubject, value: SecretStr) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(),
        b"visitor-browser-access:v1:"
        + str(visitor.id).encode()
        + b":"
        + value.get_secret_value().encode(),
        hashlib.sha256,
    ).hexdigest()
