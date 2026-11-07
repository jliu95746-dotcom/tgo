"""Retain encrypted routing for durable customer-turn delivery."""

from alembic import op
import sqlalchemy as sa

revision = "add_customer_turn_contexts"
down_revision = "generalize_message_media_envelope"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pt_customer_turn_contexts",
        sa.Column(
            "platform_id", sa.Uuid(), sa.ForeignKey("pt_platforms.id"), primary_key=True
        ),
        sa.Column("source_message_id", sa.String(255), primary_key=True),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column(
            "sequence", sa.BigInteger(), sa.Identity(), nullable=False, unique=True
        ),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("actor_key", sa.String(64), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True)),
        sa.Column("visitor_id", sa.Uuid()),
        sa.Column(
            "has_pending_media", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("media_lease_id", sa.Uuid()),
        sa.Column("media_lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_id", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column(
            "due_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_pt_customer_turn_contexts_actor_key",
        "pt_customer_turn_contexts",
        ["actor_key"],
    )
    op.create_index(
        "ix_pt_customer_turn_contexts_has_pending_media",
        "pt_customer_turn_contexts",
        ["has_pending_media"],
    )
    op.create_index(
        "ix_pt_turn_registration_queue",
        "pt_customer_turn_contexts",
        ["registered_at", "due_at", "sequence"],
    )


def downgrade():
    op.drop_table("pt_customer_turn_contexts")
