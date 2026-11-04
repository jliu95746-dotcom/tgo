"""Retain scoped script review decisions and revocation history."""

from alembic import op
import sqlalchemy as sa

revision = "l0a1b2c3d4e5"
down_revision = "k9f0a1b2c3d4"
branch_labels = None
depends_on = None
TABLE = "ai_agent_device_script_reviews"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column(
            "agent_id",
            sa.Uuid(),
            sa.ForeignKey("ai_agents.id"),
            nullable=False,
        ),
        sa.Column(
            "report_id",
            sa.Uuid(),
            sa.ForeignKey("ai_agent_device_script_trials.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "case_version_id",
            sa.Uuid(),
            sa.ForeignKey("ai_agent_device_script_cases.id"),
            nullable=False,
        ),
        sa.Column("case_digest", sa.String(64), nullable=False),
        sa.Column("result_digest", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(8), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "decision IN ('approved','rejected')",
            name="ck_ai_script_review_decision",
        ),
        sa.CheckConstraint(
            "expires_at > created_at AND (revoked_at IS NULL OR "
            "(decision = 'approved' AND revoked_at >= created_at))",
            name="ck_ai_script_review_timeline",
        ),
        sa.CheckConstraint(
            "length(case_digest) = 64 AND length(result_digest) = 64",
            name="ck_ai_script_review_digests",
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(f"LOCK TABLE {TABLE} IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text(f"SELECT count(*) FROM {TABLE}")).scalar():
        raise RuntimeError("Script review history exists; preserve decisions")
    op.drop_table(TABLE)
