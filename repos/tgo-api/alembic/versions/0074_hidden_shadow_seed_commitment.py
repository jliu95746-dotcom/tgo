"""Persist the seed commitment made at G4 shadow preregistration.

Revision ID: 0074_hidden_shadow_seed_commitment
Revises: 0073_hidden_shadow_review_source_proof
"""

import sqlalchemy as sa
from alembic import op

revision = "0074_hidden_shadow_seed_commitment"
down_revision = "0073_hidden_shadow_review_source_proof"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy rows stay uncommitted. Recomputing a digest after their original
    # registration would falsely claim that a preregistration proof existed.
    op.add_column(
        "api_hidden_shadow_protocols",
        sa.Column("seed_sha256", sa.String(64)),
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text(
        "SELECT 1 FROM api_hidden_shadow_protocols "
        "WHERE seed_sha256 IS NOT NULL LIMIT 1"
    )).first() is not None:
        raise RuntimeError(
            "Seed commitments exist; archive them before downgrade"
        )
    op.drop_column("api_hidden_shadow_protocols", "seed_sha256")
