"""Store a minimal fresh-source proof with each independent G4 review.

Revision ID: 0073_hidden_shadow_review_source_proof
Revises: 0072_hidden_shadow_gate_signoffs
"""

import sqlalchemy as sa
from alembic import op

revision = "0073_hidden_shadow_review_source_proof"
down_revision = "0072_hidden_shadow_gate_signoffs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_hidden_shadow_case_reviews",
        sa.Column("source_verified_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "api_hidden_shadow_case_reviews",
        sa.Column("source_message_seq", sa.Integer()),
    )
    op.add_column(
        "api_hidden_shadow_case_reviews",
        sa.Column("source_content_sha256", sa.String(64)),
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text(
        "SELECT 1 FROM api_hidden_shadow_case_reviews WHERE "
        "source_verified_at IS NOT NULL OR source_message_seq IS NOT NULL "
        "OR source_content_sha256 IS NOT NULL LIMIT 1"
    )).first() is not None:
        raise RuntimeError(
            "Verified source proofs exist; archive them before downgrade"
        )
    op.drop_column(
        "api_hidden_shadow_case_reviews", "source_content_sha256",
    )
    op.drop_column(
        "api_hidden_shadow_case_reviews", "source_message_seq",
    )
    op.drop_column(
        "api_hidden_shadow_case_reviews", "source_verified_at",
    )
