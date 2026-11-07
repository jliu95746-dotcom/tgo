"""Intent localization utilities."""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas.visitor import VisitorResponse


INTENT_TRANSLATIONS = {
    "purchase": {
        "en": "Purchase",
        "zh": "购买",
        "zh-cn": "购买",
        "zh_cn": "购买",
        "ja": "購入",
    },
    "inquiry": {
        "en": "Inquiry",
        "zh": "咨询",
        "zh-cn": "咨询",
        "zh_cn": "咨询",
        "ja": "問い合わせ",
    },
    "complaint": {
        "en": "Complaint",
        "zh": "投诉",
        "zh-cn": "投诉",
        "zh_cn": "投诉",
        "ja": "苦情",
    },
    "support": {
        "en": "Support",
        "zh": "支持",
        "zh-cn": "支持",
        "zh_cn": "支持",
        "ja": "サポート",
    },
}


def parse_accept_language(header: Optional[str]) -> str:
    if not header:
        return "en"
    parts = header.split(",")
    if not parts:
        return "en"
    locale = parts[0].split(";")[0].strip().lower()
    return locale or "en"


def localize_intent(
    intent: Optional[str], accept_language: Optional[str]
) -> Optional[str]:
    if not intent:
        return intent

    locale = parse_accept_language(accept_language)
    base_locale = locale.split("-")[0]
    translations = INTENT_TRANSLATIONS.get(intent)
    if not translations:
        return intent

    # Try exact match, underscore variant, base language
    candidates = [
        locale,
        locale.replace("_", "-"),
        locale.replace("-", "_"),
        base_locale,
    ]
    for candidate in candidates:
        if candidate in translations:
            return translations[candidate]
    return translations.get("en", intent)


def localize_visitor_response_intent(
    visitor_response: VisitorResponse, accept_language: Optional[str]
) -> None:
    ai_insights = getattr(visitor_response, "ai_insights", None)
    if not ai_insights:
        return

    intent_code = getattr(ai_insights, "intent", None)
    localized = localize_intent(intent_code, accept_language)
    if localized is not None:
        ai_insights.intent = localized
