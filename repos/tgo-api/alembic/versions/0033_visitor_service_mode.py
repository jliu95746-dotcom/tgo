"""Add per-visitor assist mode and humanization skill selection.

Revision ID: 0033_visitor_service_mode
Revises: 0032_global_message_idempotency
"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0033_visitor_service_mode"
down_revision: Union[str, None] = "0032_global_message_idempotency"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.add_column(
        "api_visitors",
        sa.Column("service_mode", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "api_visitors",
        sa.Column("humanization_skill_name", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "api_visitors",
        sa.Column(
            "humanization_skill_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_visitor_service_mode",
        "api_visitors",
        "service_mode IS NULL OR service_mode IN ('auto', 'assist', 'manual')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_visitor_service_mode",
        "api_visitors",
        type_="check",
    )
    op.drop_column("api_visitors", "humanization_skill_enabled")
    op.drop_column("api_visitors", "humanization_skill_name")
    op.drop_column("api_visitors", "service_mode")
