"""Record two-person G4 signoff with fixed sample and live evidence references.

Revision ID: 0063_shadow_gate_signoffs
Revises: 0062_shadow_review_adjudications
"""

from alembic import op
import sqlalchemy as sa

revision = "0063_shadow_gate_signoffs"
down_revision = "0062_shadow_review_adjudications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_shadow_gate_signoffs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("protocol_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("requester_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("review_evidence_sha256", sa.String(64), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("evidence_reference", sa.String(120), nullable=False),
        sa.Column("live_case_count", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("decision", sa.String(16)),
        sa.Column("decider_id", sa.Uuid()),
        sa.Column("decision_request_id", sa.Uuid()),
        sa.Column("decision_fingerprint", sa.String(64)),
        sa.Column("decision_note", sa.String(500)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["project_id"], ["api_projects.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["protocol_id"], ["api_shadow_review_protocols.id"],
                                ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "protocol_id", name="uq_shadow_gate_protocol"),
        sa.UniqueConstraint("project_id", "request_id", name="uq_shadow_gate_request"),
        sa.CheckConstraint("decision IS NULL OR decision IN ('accepted', 'rejected')",
                           name="ck_shadow_gate_decision"),
        sa.CheckConstraint("live_case_count > 0", name="ck_shadow_gate_live_cases"),
        sa.CheckConstraint(
            "(decision IS NULL AND decider_id IS NULL AND decision_request_id IS NULL "
            "AND decision_fingerprint IS NULL AND decision_note IS NULL "
            "AND decided_at IS NULL) OR "
            "(decision IS NOT NULL AND decider_id IS NOT NULL "
            "AND decision_request_id IS NOT NULL AND decision_fingerprint IS NOT NULL "
            "AND decision_note IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_shadow_gate_decision_complete",
        ),
    )
    op.create_index("ix_shadow_gate_project_created", "api_shadow_gate_signoffs",
                    ["project_id", "created_at"])


def downgrade() -> None:
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM api_shadow_gate_signoffs)"
    )):
        raise RuntimeError("正式影子人工签收已有记录，先受控导出再回退")
    op.drop_index("ix_shadow_gate_project_created", table_name="api_shadow_gate_signoffs")
    op.drop_table("api_shadow_gate_signoffs")
