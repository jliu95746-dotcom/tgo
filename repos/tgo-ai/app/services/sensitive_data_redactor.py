"""Deterministic redaction for customer-provided multimodal text."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.multimodal import SensitiveDataCategory


@dataclass(frozen=True)
class RedactionResult:
    """Redacted text plus stable categories that were detected."""

    text: str
    categories: tuple[SensitiveDataCategory, ...]


class SensitiveDataRedactor:
    """Remove common Chinese customer identifiers before persistence or
    prompts.
    """

    _PATTERNS: tuple[
        tuple[re.Pattern[str], str, SensitiveDataCategory], ...
    ] = (
        (
            re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
            "[手机号已脱敏]",
            SensitiveDataCategory.PHONE_NUMBER,
        ),
        (
            re.compile(r"(?<![0-9A-Za-z])\d{17}[0-9Xx](?![0-9A-Za-z])"),
            "[身份证号已脱敏]",
            SensitiveDataCategory.IDENTITY_NUMBER,
        ),
        (
            re.compile(r"((?:银行卡号?|卡号|支付账号)\s*[:：]?\s*)\d{12,19}"),
            r"\1[支付账号已脱敏]",
            SensitiveDataCategory.PAYMENT_ACCOUNT,
        ),
        (
            re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
            "[邮箱已脱敏]",
            SensitiveDataCategory.EMAIL,
        ),
        (
            re.compile(
                r"((?:收货地址|联系地址|地址)\s*[:：]\s*)"
                r"[^\n，,。;；]{4,80}"
            ),
            r"\1[地址已脱敏]",
            SensitiveDataCategory.ADDRESS,
        ),
    )

    def redact(self, text: str) -> RedactionResult:
        """Return deterministic placeholders without retaining matched
        values.
        """
        redacted = text
        categories: list[SensitiveDataCategory] = []
        for pattern, replacement, category in self._PATTERNS:
            redacted, replacement_count = pattern.subn(replacement, redacted)
            if replacement_count > 0:
                categories.append(category)
        return RedactionResult(text=redacted, categories=tuple(categories))
