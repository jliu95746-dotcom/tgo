"""Preview the next assist-draft retention batch without changing records.

Run from the tgo-api directory with:
    python -m scripts.preview_assist_draft_retention
"""

import argparse

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.services.assist_draft_retention import redact_expired_assist_drafts
from app.services.hidden_shadow_retention import (
    redact_expired_hidden_shadow_runs,
)

_REQUIRED_TABLES = (
    "api_assist_draft_records",
    "api_shadow_review_protocols",
    "api_shadow_review_protocol_candidates",
    "api_shadow_review_batches",
    "api_shadow_review_batch_items",
    "api_shadow_gate_signoffs",
    "api_hidden_shadow_runs",
    "api_hidden_shadow_protocols",
    "api_hidden_shadow_protocol_cases",
    "api_hidden_shadow_gate_signoffs",
)


def missing_retention_tables(db: Session) -> list[str]:
    """Identify an API schema that is too old to evaluate review holds."""
    existing = set(inspect(db.get_bind()).get_table_names())
    return [name for name in _REQUIRED_TABLES if name not in existing]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preview expired assist drafts and private shadow answers",
    )
    parser.add_argument(
        "--days", type=int, default=settings.ASSIST_DRAFT_RETENTION_DAYS
    )
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    if args.days < 1 or args.limit < 1:
        parser.error("--days and --limit must be positive")

    with SessionLocal() as db:
        missing = missing_retention_tables(db)
        if missing:
            message = (
                "API database migration required; missing: "
                + ", ".join(missing)
            )
            parser.exit(2, f"{message}\n")
        draft_count = redact_expired_assist_drafts(
            db, days=args.days, limit=args.limit + 1, dry_run=True
        )
        shadow_count = redact_expired_hidden_shadow_runs(
            db, days=args.days, limit=args.limit + 1, dry_run=True,
        )
    for label, count in (
        ("Eligible drafts", draft_count),
        ("Eligible private shadow answers", shadow_count),
    ):
        if count > args.limit:
            print(f"{label}: at least {count} (preview limit reached)")
        else:
            print(f"{label}: {count}")
    print("Preview only: no draft or shadow answer was changed")


if __name__ == "__main__":
    main()
