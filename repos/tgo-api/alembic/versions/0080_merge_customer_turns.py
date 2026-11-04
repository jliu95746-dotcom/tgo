"""Join production customer-turn delivery and business automation histories.

Existing revisions remain immutable. Both branches must be applied before this
single release head can be reached; this merge itself changes no business data.
"""

revision = "0080_merge_customer_turns"
down_revision = ("0048_wechat_staff_delivery", "0079_workflow_event_sources")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
