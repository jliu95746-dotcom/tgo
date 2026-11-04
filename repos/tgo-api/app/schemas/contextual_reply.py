"""Server-owned context and reviewed answers for visitor reply routing."""

from dataclasses import dataclass

from app.core.contextual_reply_config import ReviewedFAQ


@dataclass(frozen=True)
class ContextualReply:
    lookup_question: str | None = None
    reviewed_faq: ReviewedFAQ | None = None
