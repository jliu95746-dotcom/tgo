"""Record immutable script dispatch bindings and terminal metadata.

Revision ID: 0014_device_script_executions
Revises: 0013_device_script_policy
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014_device_script_executions"
down_revision = "0013_device_script_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dc_script_executions",
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dc_devices.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "execution_id", postgresql.UUID(as_uuid=True), primary_key=True
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dc_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "step_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dc_session_steps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_execution_id", postgresql.UUID(as_uuid=True)),
        sa.Column("script_id", sa.String(48), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("grant_digest", sa.String(64), nullable=False),
        sa.Column("parameters_digest", sa.String(64), nullable=False),
        sa.Column("context_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("failure", sa.String(64), nullable=False, server_default=""),
        sa.Column("result_digest", sa.String(64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("step_id", name="uq_dc_script_execution_step"),
        sa.CheckConstraint(
            "policy_revision BETWEEN 1 AND 2147483647",
            name="ck_dc_script_execution_revision",
        ),
        sa.CheckConstraint(
            "status IN ('unknown','succeeded','failed','canceled','timed_out',"
            "'output_exceeded','rejected','start_failed')",
            name="ck_dc_script_execution_status",
        ),
        sa.CheckConstraint(
            "(status = 'succeeded') = (result_digest IS NOT NULL)",
            name="ck_dc_script_execution_result",
        ),
    )
    op.create_index(
        "ix_dc_script_executions_project_id",
        "dc_script_executions",
        ["project_id"],
    )
    op.create_index(
        "ix_dc_script_executions_session_id",
        "dc_script_executions",
        ["session_id"],
    )
    op.create_index(
        "ix_dc_script_execution_grant",
        "dc_script_executions",
        ["device_id", "grant_digest"],
    )


def downgrade() -> None:
    op.execute("LOCK TABLE dc_script_executions IN ACCESS EXCLUSIVE MODE")
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM dc_script_executions)"))
        .scalar()
    ):
        raise RuntimeError(
            "Refusing to discard script execution identity history"
        )
    op.drop_index(
        "ix_dc_script_execution_grant", table_name="dc_script_executions"
    )
    op.drop_index(
        "ix_dc_script_executions_session_id", table_name="dc_script_executions"
    )
    op.drop_index(
        "ix_dc_script_executions_project_id", table_name="dc_script_executions"
    )
    op.drop_table("dc_script_executions")
