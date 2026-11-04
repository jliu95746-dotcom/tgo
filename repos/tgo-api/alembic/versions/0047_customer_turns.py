"""Persist collected customer messages, reply generations, and channel receipts."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0047_customer_turns"
down_revision = "0046_operator_authorization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_customer_turns",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id", sa.Uuid(), sa.ForeignKey("api_projects.id"), nullable=False
        ),
        sa.Column(
            "platform_id", sa.Uuid(), sa.ForeignKey("api_platforms.id"), nullable=False
        ),
        sa.Column(
            "visitor_id", sa.Uuid(), sa.ForeignKey("api_visitors.id"), nullable=False
        ),
        sa.Column("channel_id", sa.String(255), nullable=False),
        sa.Column("channel_type", sa.Integer(), nullable=False),
        sa.Column("actor_key", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("can_reply", sa.Boolean(), nullable=False),
        sa.Column("external_delivery", sa.Boolean(), nullable=False),
        sa.Column("client_msg_no", sa.String(100), nullable=False),
        sa.Column("reply_text", sa.Text()),
        sa.Column("from_uid", sa.String(255)),
        sa.Column("claim_id", sa.Uuid()),
        sa.Column("receipt", sa.String(512)),
        sa.Column("permit", postgresql.JSONB()),
        sa.Column("failure", sa.String(512)),
        sa.Column("lease_owner", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        *[
            sa.Column(name, sa.DateTime(timezone=True), nullable=False)
            for name in ("created_at", "collect_started_at", "updated_at", "due_at")
        ],
        sa.Column("ready_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("im_published_at", sa.DateTime(timezone=True)),
    )
    for name, columns in (
        ("ix_customer_turn_actor", ["actor_key", "created_at"]),
        ("ix_customer_turn_publish", ["state", "im_published_at", "due_at"]),
        ("ix_api_customer_turns_actor_key", ["actor_key"]),
        ("ix_api_customer_turns_state", ["state"]),
        ("ix_api_customer_turns_due_at", ["due_at"]),
    ):
        op.create_index(name, "api_customer_turns", columns)
    op.create_table(
        "api_customer_turn_parts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "turn_id", sa.Uuid(), sa.ForeignKey("api_customer_turns.id"), nullable=False
        ),
        sa.Column(
            "platform_id", sa.Uuid(), sa.ForeignKey("api_platforms.id"), nullable=False
        ),
        sa.Column("source_message_id", sa.String(255), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("media_pending", sa.Boolean(), nullable=False),
        sa.Column("forwarded", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_customer_turn_source",
        "api_customer_turn_parts",
        ["platform_id", "source_message_id"],
        unique=True,
    )
    op.create_index(
        "ix_api_customer_turn_parts_turn_id", "api_customer_turn_parts", ["turn_id"]
    )
    op.create_index(
        "ix_api_customer_turn_parts_forwarded", "api_customer_turn_parts", ["forwarded"]
    )


def downgrade() -> None:
    op.drop_table("api_customer_turn_parts")
    op.drop_table("api_customer_turns")
