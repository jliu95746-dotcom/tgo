"""add message analysis result persistence

Revision ID: 0028_message_analysis_results
Revises: 0027_agent_only_ai_routing
Create Date: 2026-07-16

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0028_message_analysis_results"
down_revision: Union[str, None] = "0027_agent_only_ai_routing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_media_analysis_results",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("platform_id", sa.UUID(), nullable=False),
        sa.Column("visitor_id", sa.UUID(), nullable=False),
        sa.Column("source_message_id", sa.String(length=255), nullable=False),
        sa.Column("source_media_record_id", sa.UUID(), nullable=False),
        sa.Column("media_type", sa.String(length=20), nullable=False),
        sa.Column("media_sha256", sa.String(length=64), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=True),
        sa.Column("normalized_text_is_untrusted", sa.Boolean(), nullable=False),
        sa.Column(
            "sensitive_data_categories",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("ocr_text", sa.Text(), nullable=True),
        sa.Column("vision_summary", sa.Text(), nullable=True),
        sa.Column(
            "stages",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("can_continue", sa.Boolean(), nullable=False),
        sa.Column("requires_handoff", sa.Boolean(), nullable=False),
        sa.Column("fallback_message", sa.String(length=512), nullable=True),
        sa.Column("pipeline_version", sa.String(length=128), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "media_type IN ('voice', 'image')",
            name="ck_media_analysis_media_type",
        ),
        sa.CheckConstraint(
            "status IN ('completed', 'partial', 'failed')",
            name="ck_media_analysis_status",
        ),
        sa.ForeignKeyConstraint(
            ["platform_id"], ["api_platforms.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["api_projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["visitor_id"], ["api_visitors.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "platform_id",
            "source_media_record_id",
            name="uq_media_analysis_source_media",
        ),
        sa.UniqueConstraint(
            "project_id",
            "platform_id",
            "source_message_id",
            name="uq_media_analysis_source_message",
        ),
    )
    op.create_index(
        "ix_media_analysis_project_status_created",
        "api_media_analysis_results",
        ["project_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_media_analysis_project_visitor_created",
        "api_media_analysis_results",
        ["project_id", "visitor_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "api_message_intent_results",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("platform_id", sa.UUID(), nullable=False),
        sa.Column("visitor_id", sa.UUID(), nullable=False),
        sa.Column("source_message_id", sa.String(length=255), nullable=False),
        sa.Column("media_analysis_result_id", sa.UUID(), nullable=True),
        sa.Column("intent", sa.String(length=50), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "entities",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("risk_level", sa.String(length=20), nullable=False),
        sa.Column("recommended_route", sa.String(length=30), nullable=False),
        sa.Column("need_human", sa.Boolean(), nullable=False),
        sa.Column("taxonomy_version", sa.String(length=64), nullable=False),
        sa.Column("routing_reason", sa.String(length=64), nullable=False),
        sa.Column("classification_source", sa.String(length=20), nullable=False),
        sa.Column("classifier_version", sa.String(length=128), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_message_intent_confidence",
        ),
        sa.CheckConstraint(
            "classification_source IN ('model', 'rule', 'fail_closed')",
            name="ck_message_intent_classification_source",
        ),
        sa.CheckConstraint(
            "recommended_route IN "
            "('auto_reply', 'read_only_tool', 'clarify', 'human_handoff')",
            name="ck_message_intent_route",
        ),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high')",
            name="ck_message_intent_risk_level",
        ),
        sa.ForeignKeyConstraint(
            ["media_analysis_result_id"],
            ["api_media_analysis_results.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["platform_id"], ["api_platforms.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["api_projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["visitor_id"], ["api_visitors.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "platform_id",
            "source_message_id",
            name="uq_message_intent_source_message",
        ),
    )
    op.create_index(
        "ix_message_intent_project_handoff_created",
        "api_message_intent_results",
        ["project_id", "need_human", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_message_intent_project_intent_created",
        "api_message_intent_results",
        ["project_id", "intent", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_message_intent_project_visitor_created",
        "api_message_intent_results",
        ["project_id", "visitor_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_message_intent_project_visitor_created",
        table_name="api_message_intent_results",
    )
    op.drop_index(
        "ix_message_intent_project_intent_created",
        table_name="api_message_intent_results",
    )
    op.drop_index(
        "ix_message_intent_project_handoff_created",
        table_name="api_message_intent_results",
    )
    op.drop_table("api_message_intent_results")
    op.drop_index(
        "ix_media_analysis_project_visitor_created",
        table_name="api_media_analysis_results",
    )
    op.drop_index(
        "ix_media_analysis_project_status_created",
        table_name="api_media_analysis_results",
    )
    op.drop_table("api_media_analysis_results")
