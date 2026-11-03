"""Pre-register one official G4 shadow review protocol per project.

Revision ID: 0059_shadow_review_protocols
Revises: 0058_shadow_review_batches
"""

from alembic import op
import sqlalchemy as sa

revision = "0059_shadow_review_protocols"
down_revision = "0058_shadow_review_batches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_shadow_review_protocols",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("algorithm", sa.String(32), nullable=False),
        sa.Column("random_seed", sa.String(32), nullable=False),
        sa.Column("population_count", sa.Integer(), nullable=True),
        sa.Column("population_sha256", sa.String(64), nullable=True),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"], ["api_projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["api_shadow_review_batches.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "project_id", "request_id", name="uq_shadow_protocol_project_request"
        ),
        sa.UniqueConstraint("batch_id", name="uq_shadow_protocol_batch"),
        sa.CheckConstraint(
            "window_start < window_end", name="ck_shadow_protocol_window"
        ),
        sa.CheckConstraint(
            "sample_size BETWEEN 1 AND 500",
            name="ck_shadow_protocol_sample_size",
        ),
        sa.CheckConstraint(
            "finalized_at IS NULL OR finalized_at >= created_at",
            name="ck_shadow_protocol_finalized",
        ),
        sa.CheckConstraint(
            "(finalized_at IS NULL AND batch_id IS NULL AND "
            "population_count IS NULL AND population_sha256 IS NULL) OR "
            "(finalized_at IS NOT NULL AND population_count >= 0 AND "
            "population_sha256 IS NOT NULL AND "
            "((population_count = 0 AND batch_id IS NULL) OR "
            "(population_count > 0 AND batch_id IS NOT NULL)))",
            name="ck_shadow_protocol_outcome",
        ),
    )
    op.create_index(
        "uq_shadow_protocol_active_project",
        "api_shadow_review_protocols",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("finalized_at IS NULL"),
        sqlite_where=sa.text("finalized_at IS NULL"),
    )
    op.create_index(
        "ix_shadow_protocol_project_created",
        "api_shadow_review_protocols",
        ["project_id", "created_at"],
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM api_shadow_review_protocols)")
    ):
        raise RuntimeError("正式影子验收方案已有记录，先受控导出再回退")
    op.drop_table("api_shadow_review_protocols")
