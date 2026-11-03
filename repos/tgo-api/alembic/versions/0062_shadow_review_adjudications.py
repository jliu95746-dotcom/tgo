"""Preserve independent decisions on exact disputed shadow review snapshots.

Revision ID: 0062_shadow_review_adjudications
Revises: 0061_shadow_review_manifest_digest
"""

from alembic import op
import sqlalchemy as sa

revision = "0062_shadow_review_adjudications"
down_revision = "0061_shadow_review_manifest_digest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_shadow_review_adjudications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("visitor_id", sa.Uuid(), nullable=False),
        sa.Column("adjudicator_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("review_set_sha256", sa.String(64), nullable=False),
        sa.Column("verdict", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(32)),
        sa.Column("note", sa.String(500), nullable=False),
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
            ["batch_id", "draft_id"],
            [
                "api_shadow_review_batch_items.batch_id",
                "api_shadow_review_batch_items.draft_id",
            ],
            ondelete="RESTRICT",
            name="fk_shadow_adjudication_sample_member",
        ),
        sa.CheckConstraint(
            "verdict IN ('acceptable', 'minor_issue', 'severe_error')",
            name="ck_shadow_adjudication_verdict",
        ),
        sa.UniqueConstraint(
            "project_id", "request_id", name="uq_shadow_adjudication_request"
        ),
    )
    op.create_index(
        "ix_shadow_adjudication_case_created",
        "api_shadow_review_adjudications",
        ["project_id", "batch_id", "draft_id", "created_at"],
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM api_shadow_review_adjudications)")
    ):
        raise RuntimeError("影子复核裁决已有记录，先受控导出再回退")
    op.drop_index(
        "ix_shadow_adjudication_case_created",
        table_name="api_shadow_review_adjudications",
    )
    op.drop_table("api_shadow_review_adjudications")
