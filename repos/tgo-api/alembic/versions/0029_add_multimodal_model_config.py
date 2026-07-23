"""Add project-level ASR, OCR, and VLM default model configuration.

Revision ID: 0029_multimodal_model_config
Revises: 0028_message_analysis_results
"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0029_multimodal_model_config"
down_revision: Union[str, None] = "0028_message_analysis_results"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    for capability in ("asr", "ocr", "vlm"):
        op.add_column(
            "api_project_ai_configs",
            sa.Column(
                f"default_{capability}_provider_id",
                sa.UUID(),
                nullable=True,
                comment=f"AIProvider ID for default {capability.upper()} model",
            ),
        )
        op.add_column(
            "api_project_ai_configs",
            sa.Column(
                f"default_{capability}_model",
                sa.String(length=100),
                nullable=True,
                comment=f"Default {capability.upper()} model identifier",
            ),
        )
        op.create_foreign_key(
            f"fk_project_ai_config_default_{capability}_provider",
            "api_project_ai_configs",
            "api_ai_providers",
            [f"default_{capability}_provider_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    for capability in reversed(("asr", "ocr", "vlm")):
        op.drop_constraint(
            f"fk_project_ai_config_default_{capability}_provider",
            "api_project_ai_configs",
            type_="foreignkey",
        )
        op.drop_column(
            "api_project_ai_configs", f"default_{capability}_model"
        )
        op.drop_column(
            "api_project_ai_configs", f"default_{capability}_provider_id"
        )
