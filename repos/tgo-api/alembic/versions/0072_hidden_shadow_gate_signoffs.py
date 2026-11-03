"""Bind independent G4 signoffs to frozen review evidence.

Revision ID: 0072_hidden_shadow_gate_signoffs
Revises: 0071_hidden_shadow_adjudications
"""

import sqlalchemy as sa
from alembic import op

revision = "0072_hidden_shadow_gate_signoffs"
down_revision = "0071_hidden_shadow_adjudications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_hidden_shadow_gate_signoffs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("protocol_id", sa.Uuid(), nullable=False),
        sa.Column("requester_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("review_evidence_sha256", sa.String(64), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("evidence_reference", sa.String(120), nullable=False),
        sa.Column("live_case_count", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("decision", sa.String(16)),
        sa.Column("decider_id", sa.Uuid()),
        sa.Column("decision_request_id", sa.Uuid()),
        sa.Column("decision_fingerprint", sa.String(64)),
        sa.Column("decision_note", sa.String(500)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["project_id"], ["api_projects.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["protocol_id"], ["api_hidden_shadow_protocols.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "project_id", "protocol_id", name="uq_hidden_gate_protocol",
        ),
        sa.UniqueConstraint(
            "project_id", "request_id", name="uq_hidden_gate_request",
        ),
        sa.CheckConstraint("sample_count > 0", name="ck_hidden_gate_sample"),
        sa.CheckConstraint(
            "live_case_count = sample_count", name="ck_hidden_gate_live_cases",
        ),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('accepted', 'rejected')",
            name="ck_hidden_gate_decision",
        ),
        sa.CheckConstraint(
            "(decision IS NULL AND decider_id IS NULL "
            "AND decision_request_id IS NULL AND decision_fingerprint IS NULL "
            "AND decision_note IS NULL AND decided_at IS NULL) OR "
            "(decision IS NOT NULL AND decider_id IS NOT NULL "
            "AND decision_request_id IS NOT NULL "
            "AND decision_fingerprint IS NOT NULL "
            "AND decision_note IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_hidden_gate_decision_complete",
        ),
    )
    op.create_index(
        "ix_hidden_gate_project_created",
        "api_hidden_shadow_gate_signoffs", ["project_id", "created_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text(
        "SELECT 1 FROM api_hidden_shadow_gate_signoffs LIMIT 1"
    )).first() is not None:
        raise RuntimeError(
            "Hidden shadow G4 signoffs exist; archive before downgrade"
        )
    op.drop_index(
        "ix_hidden_gate_project_created",
        table_name="api_hidden_shadow_gate_signoffs",
    )
    op.drop_table("api_hidden_shadow_gate_signoffs")
