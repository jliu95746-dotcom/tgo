"""Persist channel work in the owning assignment transaction."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0082_assignment_channel_delivery"
down_revision = "0081_agent_knowledge_catalogs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_assignment_channel_deliveries",
        sa.Column(
            "id",
            sa.Uuid(),
            sa.ForeignKey(
                "api_visitor_assignment_history.id",
                ondelete="CASCADE",
            ),
            primary_key=True,
        ),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey(
                "api_projects.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "visitor_id",
            sa.Uuid(),
            sa.ForeignKey(
                "api_visitors.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey(
                "api_visitor_sessions.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "target_staff_id",
            sa.Uuid(),
            sa.ForeignKey(
                "api_staff.id",
                ondelete="SET NULL",
            ),
        ),
        sa.Column("channel_id", sa.String(255), nullable=False),
        sa.Column("channel_type", sa.Integer(), nullable=False),
        sa.Column("effects", postgresql.JSONB(), nullable=False),
        sa.Column("effects_status", sa.String(12), nullable=False),
        sa.Column("effects_attempts", sa.Integer(), nullable=False),
        sa.Column("effects_last_error", sa.String(80)),
        sa.Column(
            "effects_updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("client_msg_no", sa.String(100), nullable=False),
        sa.Column("from_uid", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("actor_authority", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("last_error", sa.String(80)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "effects_status IN ('pending','unknown','done','skipped')",
            name="ck_assignment_channel_effects_status",
        ),
        sa.CheckConstraint(
            "status IN ('pending','sending','sent','unknown','skipped')",
            name="ck_assignment_channel_notice_status",
        ),
    )
    op.create_index(
        "ix_assignment_channel_effects_recovery",
        "api_assignment_channel_deliveries",
        ["effects_status", "effects_updated_at"],
    )
    op.create_index(
        "ix_assignment_channel_notice_recovery",
        "api_assignment_channel_deliveries",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_table("api_assignment_channel_deliveries")
