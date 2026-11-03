"""Preserve the full eligible candidate manifest for official G4 samples.

Revision ID: 0060_shadow_review_candidate_manifest
Revises: 0059_shadow_review_protocols
"""

from alembic import op
import sqlalchemy as sa

revision = "0060_shadow_review_candidate_manifest"
down_revision = "0059_shadow_review_protocols"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Alembic creates version_num as VARCHAR(32); this and later revision IDs
    # exceed that width, so widen it before Alembic records this revision.
    op.alter_column(
        "api_alembic_version", "version_num",
        existing_type=sa.String(length=32), type_=sa.String(length=128),
        existing_nullable=False,
    )
    op.create_table(
        "api_shadow_review_protocol_candidates",
        sa.Column("protocol_id", sa.Uuid(), primary_key=True),
        sa.Column("draft_id", sa.Uuid(), primary_key=True),
        sa.Column("visitor_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["protocol_id"],
            ["api_shadow_review_protocols.id"],
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM api_shadow_review_protocol_candidates)")
    ):
        raise RuntimeError("正式影子候选清单已有记录，先受控导出再回退")
    op.drop_table("api_shadow_review_protocol_candidates")
