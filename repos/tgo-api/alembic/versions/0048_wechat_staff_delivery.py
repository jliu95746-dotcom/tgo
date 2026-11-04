"""Return transferred human replies to their original WeChat bridge."""

from alembic import op
import sqlalchemy as sa

revision = "0048_wechat_staff_delivery"
down_revision = "0047_customer_turns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_staff_delivery_external", "api_staff_message_deliveries", type_="check"
    )
    op.create_check_constraint(
        "ck_staff_delivery_external",
        "api_staff_message_deliveries",
        "external_status IN ('not_required','queued','sending','sent','failed','unknown')",
    )
    op.create_table(
        "api_wechat_staff_deliveries",
        sa.Column(
            "id",
            sa.Uuid(),
            sa.ForeignKey("api_staff_message_deliveries.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "project_id", sa.Uuid(), sa.ForeignKey("api_projects.id"), nullable=False
        ),
        sa.Column(
            "platform_id", sa.Uuid(), sa.ForeignKey("api_platforms.id"), nullable=False
        ),
        sa.Column(
            "visitor_id", sa.Uuid(), sa.ForeignKey("api_visitors.id"), nullable=False
        ),
        sa.Column("platform_open_id", sa.String(255), nullable=False),
        sa.Column(
            "owner_session_id",
            sa.Uuid(),
            sa.ForeignKey("api_visitor_sessions.id"),
            nullable=False,
        ),
        sa.Column(
            "owner_staff_id", sa.Uuid(), sa.ForeignKey("api_staff.id"), nullable=False
        ),
        sa.Column("owner_revision", sa.Uuid()),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("attempt_id", sa.Uuid()),
        sa.Column("claim_id", sa.Uuid()),
        sa.Column("receipt", sa.String(512)),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('queued','sending','sent','unknown','cancelled')",
            name="ck_wechat_staff_state",
        ),
    )
    op.create_index(
        "ix_wechat_staff_pending",
        "api_wechat_staff_deliveries",
        ["platform_id", "visitor_id", "state", "created_at"],
    )


def downgrade() -> None:
    # Pending/uncertain real sends must be reconciled before deleting their fence.
    count = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM api_wechat_staff_deliveries WHERE state IN ('queued','sending','unknown')"
            )
        )
        .scalar()
    )
    if count:
        raise RuntimeError("Reconcile pending WeChat staff deliveries before downgrade")
    op.drop_table("api_wechat_staff_deliveries")
    op.drop_constraint(
        "ck_staff_delivery_external", "api_staff_message_deliveries", type_="check"
    )
    op.create_check_constraint(
        "ck_staff_delivery_external",
        "api_staff_message_deliveries",
        "external_status IN ('not_required','sending','sent','failed','unknown')",
    )
