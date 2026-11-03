"""Record a digest of every frozen draft-to-visitor mapping.

Revision ID: 0061_shadow_review_manifest_digest
Revises: 0060_shadow_review_candidate_manifest
"""

from alembic import op
import sqlalchemy as sa

revision = "0061_shadow_review_manifest_digest"
down_revision = "0060_shadow_review_candidate_manifest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_shadow_review_protocols",
        sa.Column("manifest_sha256", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM api_shadow_review_protocols "
            "WHERE manifest_sha256 IS NOT NULL)"
        )
    ):
        raise RuntimeError("正式影子候选映射摘要已有记录，先受控导出再回退")
    op.drop_column("api_shadow_review_protocols", "manifest_sha256")
