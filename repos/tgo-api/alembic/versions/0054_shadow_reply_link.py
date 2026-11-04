"""Record shadow intent mode and the customer message answered by staff.

Revision ID: 0054_shadow_reply_link
Revises: 0053_session_channel_effects
"""

from alembic import op
import sqlalchemy as sa

revision = "0054_shadow_reply_link"
down_revision = "0053_session_channel_effects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_message_intent_results",
        sa.Column(
            "analysis_mode", sa.String(12), nullable=False, server_default="active"
        ),
    )
    op.create_check_constraint(
        "ck_message_intent_analysis_mode",
        "api_message_intent_results",
        "analysis_mode IN ('active', 'shadow')",
    )
    op.add_column(
        "api_staff_message_deliveries",
        sa.Column("source_message_id", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_staff_delivery_shadow_source",
        "api_staff_message_deliveries",
        ["project_id", "visitor_id", "source_message_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    has_shadow_intents = connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM api_message_intent_results "
            "WHERE analysis_mode = 'shadow')"
        )
    )
    has_reply_links = connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM api_staff_message_deliveries "
            "WHERE source_message_id IS NOT NULL)"
        )
    )
    if has_shadow_intents or has_reply_links:
        raise RuntimeError("影子对照已有记录，先完成受控导出和数据迁移后再回退")
    op.drop_index(
        "ix_staff_delivery_shadow_source", table_name="api_staff_message_deliveries"
    )
    op.drop_column("api_staff_message_deliveries", "source_message_id")
    op.drop_constraint(
        "ck_message_intent_analysis_mode",
        "api_message_intent_results",
        type_="check",
    )
    op.drop_column("api_message_intent_results", "analysis_mode")
