"""Persist employee role/channel selections without changing serving employees."""

from alembic import op
import sqlalchemy as sa

revision = "v4w5x6y7z8a9"
down_revision = "u3v4w5x6y7z8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_agent_activation_policies",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", sa.UUID(as_uuid=True), sa.ForeignKey("ai_agents.id"), nullable=False),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("template_id", sa.String(50), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("requirements_revision", sa.String(64), nullable=False),
        sa.Column("source_draft_id", sa.UUID(as_uuid=True)),
        sa.Column("source_draft_revision", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("project_id", "agent_id", "channel", name="uq_ai_agent_policy_channel"),
        sa.CheckConstraint("revision >= 1 AND template_version >= 1", name="ck_ai_agent_policy_versions"),
        sa.CheckConstraint(
            "(source_draft_id IS NULL AND source_draft_revision IS NULL) OR "
            "(source_draft_id IS NOT NULL AND source_draft_revision IS NOT NULL AND source_draft_revision >= 1)",
            name="ck_ai_agent_policy_source",
        ),
    )
    for column in ("project_id", "agent_id"):
        op.create_index(f"ix_ai_agent_activation_policies_{column}", "ai_agent_activation_policies", [column])


def downgrade() -> None:
    # Restore compatible consumers and back up policy metadata before dropping it.
    op.drop_table("ai_agent_activation_policies")
