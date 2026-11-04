"""Persist an explicit approval selection and its original write receipt."""

from alembic import op
import sqlalchemy as sa

revision = "o3d4e5f6a7b8"
down_revision = "n2c3d4e5f6a7"
branch_labels = None
depends_on = None
SELECTION = "ai_agent_device_script_selections"
REQUEST = "ai_agent_device_script_selection_requests"


def _base_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column(
            "agent_id",
            sa.Uuid(),
            sa.ForeignKey("ai_agents.id"),
            nullable=False,
        ),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    ]


def upgrade() -> None:
    op.create_table(
        SELECTION,
        *_base_columns(),
        sa.Column("configured_by", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "binding_id",
            sa.Uuid(),
            sa.ForeignKey("ai_agent_device_script_bindings.id"),
            nullable=False,
        ),
        sa.Column("binding_revision", sa.Integer(), nullable=False),
        sa.Column(
            "report_id",
            sa.Uuid(),
            sa.ForeignKey("ai_agent_device_script_trials.id"),
            nullable=False,
        ),
        sa.Column(
            "review_id",
            sa.Uuid(),
            sa.ForeignKey("ai_agent_device_script_reviews.id"),
            nullable=False,
        ),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("selection_digest", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "project_id", "agent_id", name="uq_ai_script_selection_agent"
        ),
        sa.CheckConstraint(
            "revision >= 1 AND revision <= 2147483647 AND "
            "binding_revision >= 1 AND binding_revision <= 2147483647",
            name="ck_ai_script_selection_revision",
        ),
        sa.CheckConstraint(
            "length(selection_digest) = 64",
            name="ck_ai_script_selection_digest",
        ),
        sa.CheckConstraint(
            "NOT enabled OR expires_at > saved_at",
            name="ck_ai_script_selection_time",
        ),
    )
    op.create_table(
        REQUEST,
        *_base_columns(),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("command_digest", sa.String(64), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("result_digest", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "requested_by",
            "request_id",
            name="uq_ai_script_selection_request",
        ),
        sa.CheckConstraint(
            "length(command_digest) = 64 AND length(result_digest) = 64 "
            "AND length(result_json) <= 8192",
            name="ck_ai_script_selection_receipt",
        ),
    )
    for table in (SELECTION, REQUEST):
        op.create_index(f"ix_{table}_project_id", table, ["project_id"])


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            f"LOCK TABLE {SELECTION}, {REQUEST} IN ACCESS EXCLUSIVE MODE",
        )
    )
    for table in (SELECTION, REQUEST):
        if connection.execute(
            sa.text(
                f"SELECT 1 FROM {table} LIMIT 1",
            )
        ).scalar():
            raise RuntimeError("Script selection history exists; preserve it")
    op.drop_table(REQUEST)
    op.drop_table(SELECTION)
