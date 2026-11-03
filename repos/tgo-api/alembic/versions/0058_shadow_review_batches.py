"""Freeze G4 shadow review sample membership.

Revision ID: 0058_shadow_review_batches
Revises: 0057_assist_draft_source_proof
"""

from alembic import op
import sqlalchemy as sa

revision = "0058_shadow_review_batches"
down_revision = "0057_assist_draft_source_proof"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_assist_draft_project_created",
        "api_assist_draft_records",
        ["project_id", "created_at"],
    )
    op.create_table(
        "api_shadow_review_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("random_seed", sa.String(32), nullable=False),
        sa.Column("algorithm", sa.String(32), nullable=False),
        sa.Column("population_sha256", sa.String(64), nullable=False),
        sa.Column("population_count", sa.Integer(), nullable=False),
        sa.Column("requested_size", sa.Integer(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["api_projects.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "project_id",
            "request_id",
            name="uq_shadow_review_batch_project_request",
        ),
        sa.CheckConstraint(
            "window_start < window_end",
            name="ck_shadow_review_batch_window",
        ),
        sa.CheckConstraint(
            "requested_size BETWEEN 1 AND 500 AND "
            "sample_count BETWEEN 1 AND requested_size AND "
            "population_count >= sample_count",
            name="ck_shadow_review_batch_counts",
        ),
    )
    op.create_index(
        "ix_shadow_review_batch_project_created",
        "api_shadow_review_batches",
        ["project_id", "created_at"],
    )
    op.create_table(
        "api_shadow_review_batch_items",
        sa.Column("batch_id", sa.Uuid(), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("visitor_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["api_shadow_review_batches.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "batch_id", "draft_id", name="uq_shadow_review_batch_draft"
        ),
        sa.CheckConstraint("position > 0", name="ck_shadow_review_batch_position"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    has_batches = connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM api_shadow_review_batches)")
    )
    if has_batches:
        raise RuntimeError("影子抽样批次已有固定成员，先受控导出再回退")
    op.drop_table("api_shadow_review_batch_items")
    op.drop_index(
        "ix_shadow_review_batch_project_created",
        table_name="api_shadow_review_batches",
    )
    op.drop_table("api_shadow_review_batches")
    op.drop_index(
        "ix_assist_draft_project_created",
        table_name="api_assist_draft_records",
    )
