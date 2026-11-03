"""Persist employee script configuration without granting execution."""

from alembic import op
import sqlalchemy as sa

revision = "g5b6c7d8e9f0"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def common() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column(
            "agent_id",
            sa.Uuid(),
            sa.ForeignKey("ai_agents.id"),
            nullable=False,
        ),
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
    ]


def upgrade() -> None:
    op.create_table(
        "ai_agent_device_script_bindings",
        *common(),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("script_id", sa.String(48), nullable=False),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("grant_digest", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("configured_by", sa.Uuid(), nullable=False),
        sa.UniqueConstraint(
            "project_id", "agent_id", name="uq_ai_agent_script_binding"
        ),
        sa.CheckConstraint(
            "revision >= 1", name="ck_ai_agent_script_revision"
        ),
        sa.CheckConstraint(
            "length(policy_digest) = 64 AND length(grant_digest) = 64",
            name="ck_ai_agent_script_digests",
        ),
    )
    op.create_table(
        "ai_agent_device_script_requests",
        *common(),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("command_digest", sa.String(64), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "requested_by",
            "request_id",
            name="uq_ai_agent_script_request",
        ),
    )
    for table in (
        "ai_agent_device_script_bindings",
        "ai_agent_device_script_requests",
    ):
        for column in ("project_id", "agent_id"):
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    connection = op.get_bind()
    tables = (
        "ai_agent_device_script_bindings",
        "ai_agent_device_script_requests",
    )
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                "LOCK TABLE ai_agent_device_script_bindings, "
                "ai_agent_device_script_requests IN ACCESS EXCLUSIVE MODE"
            )
        )
    if any(
        connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar()
        for table in tables
    ):
        raise RuntimeError(
            "Employee script records exist; use a compatible rollback"
        )
    for table in reversed(tables):
        op.drop_table(table)
