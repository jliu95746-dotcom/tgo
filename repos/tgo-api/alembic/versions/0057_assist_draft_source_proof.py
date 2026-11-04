"""Persist server IM source proof for newly generated assist drafts.

Revision ID: 0057_assist_draft_source_proof
Revises: 0056_shadow_draft_reviews
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0057_assist_draft_source_proof"
down_revision = "0056_shadow_draft_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_assist_draft_records",
        sa.Column("source_proof", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    connection = op.get_bind()
    has_proof = connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM api_assist_draft_records "
            "WHERE source_proof IS NOT NULL)"
        )
    )
    if has_proof:
        raise RuntimeError("AI 草稿已有 IM 来源核验记录，先完成受控导出和数据迁移后再回退")
    op.drop_column("api_assist_draft_records", "source_proof")
