from __future__ import annotations
from app.domain.entities import NormalizedMessage
from app.domain.ports import MessageNormalizer


class DefaultMessageNormalizer(MessageNormalizer):
    async def normalize(self, raw: dict) -> NormalizedMessage:
        # Minimal pass-through normalizer. Expects platform fields provided by listeners/callbacks.
        return NormalizedMessage(
            source=raw.get("source", "webhook"),
            from_uid=raw["from_uid"],
            content=raw["content"],
            platform_api_key=raw["platform_api_key"],
            platform_type=raw["platform_type"],
            platform_id=raw["platform_id"],
            extra=raw.get("extra", {}),
        )


normalizer = DefaultMessageNormalizer()
