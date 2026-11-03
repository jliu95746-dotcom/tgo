"""Retain immutable case versions and purpose-encrypted actual results."""

from alembic import op
import sqlalchemy as sa

revision = "j8e9f0a1b2c3"
down_revision = "i7d8e9f0a1b2"
branch_labels = None
depends_on = None

CASES = "ai_agent_device_script_cases"
EVIDENCE = "ai_agent_device_script_evidence"


def audit_columns():
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        CASES,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column(
            "agent_id",
            sa.Uuid(),
            sa.ForeignKey("ai_agents.id"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column(
            "binding_id",
            sa.Uuid(),
            sa.ForeignKey("ai_agent_device_script_bindings.id"),
            nullable=False,
        ),
        sa.Column("binding_revision", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("grant_digest", sa.String(64), nullable=False),
        sa.Column("command_digest", sa.String(64), nullable=False),
        sa.Column("case_digest", sa.String(64), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        *audit_columns(),
        sa.UniqueConstraint(
            "project_id",
            "agent_id",
            "case_id",
            "revision",
            name="uq_ai_script_case_version",
        ),
        sa.UniqueConstraint(
            "project_id",
            "created_by",
            "request_id",
            name="uq_ai_script_case_write",
        ),
        sa.CheckConstraint(
            "revision >= 1 AND binding_revision >= 1",
            name="ck_ai_script_case_revision",
        ),
        sa.CheckConstraint(
            "length(title) BETWEEN 1 AND 80", name="ck_ai_script_case_title"
        ),
        sa.CheckConstraint(
            "length(command_digest) = 64 AND length(case_digest) = 64 AND "
            "length(policy_digest) = 64 AND length(grant_digest) = 64",
            name="ck_ai_script_case_digests",
        ),
        sa.CheckConstraint(
            "(ciphertext IS NOT NULL AND cleared_at IS NULL AND "
            "length(ciphertext) <= 200000) OR "
            "(ciphertext IS NULL AND cleared_at IS NOT NULL)",
            name="ck_ai_script_case_body",
        ),
    )
    op.create_table(
        EVIDENCE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column(
            "agent_id",
            sa.Uuid(),
            sa.ForeignKey("ai_agents.id"),
            nullable=False,
        ),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column(
            "case_version_id",
            sa.Uuid(),
            sa.ForeignKey(CASES + ".id"),
            nullable=False,
        ),
        sa.Column("case_digest", sa.String(64), nullable=False),
        sa.Column("command_digest", sa.String(64), nullable=False),
        sa.Column(
            "report_id",
            sa.Uuid(),
            sa.ForeignKey("ai_agent_device_script_trials.id"),
            nullable=True,
        ),
        sa.Column("result_digest", sa.String(64), nullable=True),
        sa.Column("ciphertext", sa.Text(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        *audit_columns(),
        sa.UniqueConstraint(
            "project_id",
            "actor_id",
            "request_id",
            name="uq_ai_script_evidence_request",
        ),
        sa.UniqueConstraint("report_id", name="uq_ai_script_evidence_report"),
        sa.CheckConstraint(
            "length(command_digest) = 64 AND length(case_digest) = 64 AND "
            "(result_digest IS NULL OR length(result_digest) = 64)",
            name="ck_ai_script_evidence_digests",
        ),
        sa.CheckConstraint(
            "(captured_at IS NULL AND result_digest IS NULL AND "
            "ciphertext IS NULL) OR (captured_at IS NOT NULL AND "
            "report_id IS NOT NULL AND result_digest IS NOT NULL AND "
            "((ciphertext IS NOT NULL AND cleared_at IS NULL AND "
            "length(ciphertext) <= 200000) OR "
            "(ciphertext IS NULL AND cleared_at IS NOT NULL)))",
            name="ck_ai_script_evidence_body",
        ),
    )
    for table, columns in (
        (CASES, ("project_id", "agent_id", "expires_at")),
        (
            EVIDENCE,
            ("project_id", "agent_id", "case_version_id", "expires_at"),
        ),
    ):
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(f"LOCK TABLE {EVIDENCE}, {CASES} IN ACCESS EXCLUSIVE MODE")
    )
    for table in (EVIDENCE, CASES):
        if connection.execute(
            sa.text(f"SELECT count(*) FROM {table}")
        ).scalar():
            raise RuntimeError("Script case history exists; preserve evidence")
    op.drop_table(EVIDENCE)
    op.drop_table(CASES)
