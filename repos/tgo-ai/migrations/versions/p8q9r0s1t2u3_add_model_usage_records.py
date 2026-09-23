"""Persist internal model usage without changing existing agents or reply quotas."""

from alembic import op
import sqlalchemy as sa

revision = "p8q9r0s1t2u3"
down_revision = "o7p8q9r0s1t2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_model_usage_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("reservation_id", sa.Uuid()),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("purpose", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("input_rate_fen", sa.Numeric(18, 6)),
        sa.Column("output_rate_fen", sa.Numeric(18, 6)),
        sa.Column("estimated_cost_fen", sa.Numeric(30, 8)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('running','succeeded','failed','cancelled')",
            name="ck_model_usage_status",
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="ck_model_usage_input"
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="ck_model_usage_output"
        ),
        sa.CheckConstraint(
            "estimated_cost_fen IS NULL OR estimated_cost_fen >= 0",
            name="ck_model_usage_cost",
        ),
    )
    op.create_index(
        "ix_ai_model_usage_records_project_id", "ai_model_usage_records", ["project_id"]
    )
    op.create_index(
        "ix_ai_model_usage_records_reservation_id",
        "ai_model_usage_records",
        ["reservation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_model_usage_records_reservation_id", table_name="ai_model_usage_records"
    )
    op.drop_index(
        "ix_ai_model_usage_records_project_id", table_name="ai_model_usage_records"
    )
    op.drop_table("ai_model_usage_records")
