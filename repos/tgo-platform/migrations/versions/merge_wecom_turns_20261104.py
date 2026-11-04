"""Join durable customer-turn delivery and WeCom failure-review migrations."""

revision = "merge_wecom_turns_20261104"
down_revision = (
    "add_customer_turn_contexts", "backfill_wecom_failure_notice_review",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
