"""Persist committed session commands' channel effects for safe recovery.

Revision ID: 0053_session_channel_effects
Revises: 0052_session_command_deliveries
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0053_session_channel_effects"
down_revision = "0052_session_command_deliveries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = "api_session_command_deliveries"
    op.add_column(
        table,
        sa.Column(
            "effects", JSONB(), nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        table,
        sa.Column(
            "effects_status", sa.String(12), nullable=False,
            server_default="done",
        ),
    )
    op.add_column(
        table,
        sa.Column(
            "effects_attempts", sa.Integer(), nullable=False,
            server_default="0",
        ),
    )
    op.add_column(table, sa.Column("effects_last_error", sa.String(80)))
    op.add_column(
        table,
        sa.Column(
            "effects_updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_check_constraint(
        "ck_session_command_effects_status", table,
        "effects_status IN ('pending','done','unknown','skipped')",
    )
    op.create_index(
        "ix_session_command_effects_recovery", table,
        ["effects_status", "effects_updated_at"],
    )


def downgrade() -> None:
    table = "api_session_command_deliveries"
    if op.get_bind().scalar(sa.text(f"SELECT count(*) FROM {table}")):
        raise RuntimeError("会话操作存在频道副作用记录，请使用兼容代码回退")
    op.drop_index("ix_session_command_effects_recovery", table_name=table)
    op.drop_constraint("ck_session_command_effects_status", table, type_="check")
    for column in (
        "effects_updated_at", "effects_last_error", "effects_attempts",
        "effects_status", "effects",
    ):
        op.drop_column(table, column)
