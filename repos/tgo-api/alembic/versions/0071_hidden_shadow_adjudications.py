"""Add independent G4 third-person adjudications.

Revision ID: 0071_hidden_shadow_adjudications
Revises: 0070_hidden_shadow_case_reviews
"""

import sqlalchemy as sa
from alembic import op

revision = "0071_hidden_shadow_adjudications"
down_revision = "0070_hidden_shadow_case_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_hidden_shadow_adjudications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("protocol_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("adjudicator_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("review_set_sha256", sa.String(64), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("verdict", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(32)),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["api_projects.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["protocol_id", "candidate_id"],
            [
                "api_hidden_shadow_protocol_cases.protocol_id",
                "api_hidden_shadow_protocol_cases.candidate_id",
            ],
            ondelete="RESTRICT",
            name="fk_hidden_adjudication_frozen_case",
        ),
        sa.UniqueConstraint(
            "protocol_id", "candidate_id",
            name="uq_hidden_adjudication_case",
        ),
        sa.UniqueConstraint(
            "project_id", "request_id",
            name="uq_hidden_adjudication_request",
        ),
        sa.CheckConstraint(
            "verdict IN ('acceptable', 'minor_issue', 'severe_error')",
            name="ck_hidden_adjudication_verdict",
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR reason_code IN "
            "('wrong_fact', 'privacy', 'unsafe_action', "
            "'missing_handoff', 'other')",
            name="ck_hidden_adjudication_reason",
        ),
        sa.CheckConstraint(
            "length(trim(note)) > 0", name="ck_hidden_adjudication_note",
        ),
    )
    op.create_index(
        "ix_hidden_adjudication_protocol",
        "api_hidden_shadow_adjudications", ["protocol_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text(
        "SELECT 1 FROM api_hidden_shadow_adjudications LIMIT 1"
    )).first() is not None:
        raise RuntimeError(
            "Hidden shadow adjudications exist; archive before downgrade"
        )
    op.drop_index(
        "ix_hidden_adjudication_protocol",
        table_name="api_hidden_shadow_adjudications",
    )
    op.drop_table("api_hidden_shadow_adjudications")
