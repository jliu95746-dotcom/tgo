"""Add append-only human review records for shadow assist drafts.

Revision ID: 0056_shadow_draft_reviews
Revises: 0055_assist_draft_records
"""

from alembic import op
import sqlalchemy as sa

revision = "0056_shadow_draft_reviews"
down_revision = "0055_assist_draft_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_shadow_draft_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("visitor_id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("verdict", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(32), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["api_projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["visitor_id"], ["api_visitors.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"], ["api_assist_draft_records.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"], ["api_staff.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "project_id",
            "reviewer_id",
            "request_id",
            name="uq_shadow_review_reviewer_request",
        ),
        sa.CheckConstraint(
            "verdict IN ('acceptable', 'minor_issue', "
            "'severe_error', 'insufficient_evidence')",
            name="ck_shadow_review_verdict",
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR reason_code IN "
            "('wrong_fact', 'privacy', 'unsafe_action', "
            "'missing_handoff', 'other')",
            name="ck_shadow_review_reason",
        ),
    )
    op.create_index(
        "ix_shadow_review_project_draft_created",
        "api_shadow_draft_reviews",
        ["project_id", "draft_id", "created_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    has_reviews = connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM api_shadow_draft_reviews)")
    )
    if has_reviews:
        raise RuntimeError("影子人工复核已有审计记录，先完成受控导出和数据迁移后再回退")
    op.drop_index(
        "ix_shadow_review_project_draft_created",
        table_name="api_shadow_draft_reviews",
    )
    op.drop_table("api_shadow_draft_reviews")
