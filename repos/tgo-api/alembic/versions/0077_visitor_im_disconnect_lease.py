"""Serialize live visitor IM disconnect requests across API instances.

Revision ID: 0077_visitor_im_disconnect_lease
Revises: 0076_visitor_end_receipts
"""

import sqlalchemy as sa
from alembic import op

revision = "0077_visitor_im_disconnect_lease"
down_revision = "0076_visitor_end_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_visitors",
        sa.Column("im_disconnect_lease_until", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "api_visitors",
        sa.Column("im_disconnect_lease_token", sa.Uuid()),
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text(
        "SELECT 1 FROM api_visitors "
        "WHERE im_disconnect_lease_until IS NOT NULL "
        "OR im_disconnect_lease_token IS NOT NULL LIMIT 1"
    )).first() is not None:
        raise RuntimeError(
            "Visitor IM disconnect leases exist; "
            "wait or archive before downgrade"
        )
    op.drop_column("api_visitors", "im_disconnect_lease_token")
    op.drop_column("api_visitors", "im_disconnect_lease_until")
