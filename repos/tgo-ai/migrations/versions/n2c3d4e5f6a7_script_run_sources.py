"""Keep runtime provenance distinct from the original reviewing actor."""

from alembic import op
import sqlalchemy as sa

revision = "n2c3d4e5f6a7"
down_revision = "m1b2c3d4e5f6"
branch_labels = None
depends_on = None
TABLE = "ai_agent_device_script_runs"
INDEX = f"ix_{TABLE}_agent_execution_id"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("agent_execution_id", sa.Uuid()))
    op.add_column(TABLE, sa.Column("source_json", sa.Text()))
    op.add_column(TABLE, sa.Column("source_digest", sa.String(64)))
    op.create_check_constraint(
        "ck_ai_script_run_source", TABLE,
        "(agent_execution_id IS NULL AND source_json IS NULL "
        "AND source_digest IS NULL) OR (agent_execution_id IS NOT NULL "
        "AND source_json IS NOT NULL AND length(source_json) <= 4096 "
        "AND source_digest IS NOT NULL AND length(source_digest) = 64)",
    )
    op.create_index(INDEX, TABLE, ["agent_execution_id"])


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(
        f"LOCK TABLE {TABLE} IN ACCESS EXCLUSIVE MODE",
    ))
    if connection.execute(sa.text(
        f"SELECT 1 FROM {TABLE} WHERE agent_execution_id IS NOT NULL "
        "OR source_json IS NOT NULL OR source_digest IS NOT NULL LIMIT 1",
    )).scalar():
        raise RuntimeError(
            "Runtime source history exists; preserve provenance",
        )
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_constraint("ck_ai_script_run_source", TABLE, type_="check")
    op.drop_column(TABLE, "source_digest")
    op.drop_column(TABLE, "source_json")
    op.drop_column(TABLE, "agent_execution_id")
