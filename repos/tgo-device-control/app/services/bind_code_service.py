"""Redis-based bind code service."""

import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import redis.asyncio as redis

from app.config import settings
from app.core.logging import get_logger

logger = get_logger("services.bind_code_service")


class BindCodeService:
    """Service for managing device bind codes in Redis."""

    KEY_PREFIX = "dc:bind_code:"
    ATTEMPT_PREFIX = "dc:bind_attempts:"
    MAX_ATTEMPTS = 5
    ATTEMPT_WINDOW = 3600  # 1 hour
    # Redis 6 compatible atomic consume (GETDEL requires Redis 6.2).
    CONSUME_SCRIPT = """
        local value = redis.call('GET', KEYS[1])
        if value then redis.call('DEL', KEYS[1]) end
        return value
    """

    def __init__(self):
        self.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)

    def _generate_code(self) -> str:
        """Generate a random alphanumeric code."""
        return "".join(
            secrets.choice(string.ascii_uppercase + string.digits)
            for _ in range(settings.BIND_CODE_LENGTH)
        )

    async def generate(self, project_id: uuid.UUID) -> Tuple[str, datetime]:
        """
        Generate a unique bind code and store it in Redis.
        Returns the code and its expiration time.
        """
        for _ in range(5):
            code = self._generate_code()
            key = f"{self.KEY_PREFIX}{code}"
            try:
                success = await self.redis.set(
                    key,
                    str(project_id),
                    nx=True,
                    ex=settings.BIND_CODE_EXPIRY_MINUTES * 60,
                )
                if success:
                    expires_at = datetime.now(timezone.utc) + timedelta(
                        minutes=settings.BIND_CODE_EXPIRY_MINUTES
                    )
                    return code, expires_at
            except Exception as e:
                logger.error("Bind code creation failed: %s", type(e).__name__)
                raise

        logger.error("[DEBUG] Failed to generate a unique bind code after 5 attempts")
        raise Exception("Failed to generate unique bind code")

    async def validate(self, code: str) -> Optional[uuid.UUID]:
        """
        Validate a bind code and return the associated project_id.
        The code is deleted after successful validation.
        """
        if not isinstance(code, str) or len(code) != settings.BIND_CODE_LENGTH:
            return None
        key = f"{self.KEY_PREFIX}{code.upper()}"
        try:
            project_id_str = await self.redis.eval(self.CONSUME_SCRIPT, 1, key)
        except Exception as e:
            logger.error("Bind code validation failed: %s", type(e).__name__)
            return None

        if not project_id_str:
            return None

        try:
            return uuid.UUID(project_id_str)
        except ValueError:
            logger.error("Invalid project ID stored for bind code")
            return None

    async def check_rate_limit(self, identifier: str) -> bool:
        """
        Basic rate limiting for bind code attempts.
        Returns True if allowed, False if rate limited.
        """
        key = f"{self.ATTEMPT_PREFIX}{identifier}"
        attempts = await self.redis.get(key)

        if attempts and int(attempts) >= self.MAX_ATTEMPTS:
            return False

        return True

    async def record_attempt(self, identifier: str):
        """Record a failed bind code attempt."""
        key = f"{self.ATTEMPT_PREFIX}{identifier}"
        await self.redis.incr(key)
        await self.redis.expire(key, self.ATTEMPT_WINDOW)


# Global singleton instance
bind_code_service = BindCodeService()
