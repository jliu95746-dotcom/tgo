"""Record each controlled transition from an old workflow binding to a fixed release.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
"""

from alembic import op
import sqlalchemy as sa

revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_agent_workflow_pin_transitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("binding_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.String(36), nullable=False),
        sa.Column("old_release_version", sa.Integer(), nullable=True),
        sa.Column("old_release_id", sa.String(36), nullable=True),
        sa.Column("old_release_digest", sa.String(64), nullable=True),
        sa.Column("release_version", sa.Integer(), nullable=False),
        sa.Column("release_id", sa.String(36), nullable=False),
        sa.Column("release_digest", sa.String(64), nullable=False),
        sa.Column("agent_active", sa.Boolean(), nullable=False),
        sa.Column("binding_enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "project_id",
            "requested_by",
            "request_id",
            name="uq_ai_workflow_pin_request",
        ),
        sa.UniqueConstraint("binding_id", name="uq_ai_workflow_legacy_pin_binding"),
    )
    op.create_index(
        "ix_ai_agent_workflow_pin_transitions_project_id",
        "ai_agent_workflow_pin_transitions",
        ["project_id"],
    )
    op.create_index(
        "ix_ai_agent_workflow_pin_transitions_agent_id",
        "ai_agent_workflow_pin_transitions",
        ["agent_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                "LOCK TABLE ai_agent_workflow_pin_transitions IN ACCESS EXCLUSIVE MODE"
            )
        )
    if (
        connection.execute(
            sa.text("SELECT 1 FROM ai_agent_workflow_pin_transitions LIMIT 1")
        ).scalar_one_or_none()
        is not None
    ):
        raise RuntimeError(
            "Workflow pin transition history exists; use a compatible rollback"
        )
    op.drop_index(
        "ix_ai_agent_workflow_pin_transitions_agent_id",
        table_name="ai_agent_workflow_pin_transitions",
    )
    op.drop_index(
        "ix_ai_agent_workflow_pin_transitions_project_id",
        table_name="ai_agent_workflow_pin_transitions",
    )
    op.drop_table("ai_agent_workflow_pin_transitions")
