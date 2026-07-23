"""Add project-level ASR, OCR, and VLM defaults.

Revision ID: l4m5n6o7p8q9
Revises: k3l4m5n6o7p8
"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "l4m5n6o7p8q9"
down_revision: Union[str, None] = "k3l4m5n6o7p8"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    for capability in ("asr", "ocr", "vlm"):
        op.add_column(
            "ai_project_ai_configs",
            sa.Column(
                f"default_{capability}_provider_id",
                sa.UUID(),
                nullable=True,
                comment=f"Provider id for default {capability.upper()} model",
            ),
        )
        op.add_column(
            "ai_project_ai_configs",
            sa.Column(
                f"default_{capability}_model",
                sa.String(length=150),
                nullable=True,
                comment=f"Default {capability.upper()} model name",
            ),
        )


def downgrade() -> None:
    for capability in reversed(("asr", "ocr", "vlm")):
        op.drop_column(
            "ai_project_ai_configs", f"default_{capability}_model"
        )
        op.drop_column(
            "ai_project_ai_configs", f"default_{capability}_provider_id"
        )
