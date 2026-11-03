"""Add separate append-only reviews for independent G4 samples.

Revision ID: 0070_hidden_shadow_case_reviews
Revises: 0069_hidden_shadow_protocols
"""

import sqlalchemy as sa
from alembic import op

revision = "0070_hidden_shadow_case_reviews"
down_revision = "0069_hidden_shadow_protocols"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_hidden_shadow_case_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("protocol_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("verdict", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(32)),
        sa.Column("note", sa.String(500)),
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
            ondelete="RESTRICT", name="fk_hidden_review_frozen_case",
        ),
        sa.UniqueConstraint(
            "protocol_id", "candidate_id", "reviewer_id",
            name="uq_hidden_review_case_reviewer",
        ),
        sa.UniqueConstraint(
            "project_id", "reviewer_id", "request_id",
            name="uq_hidden_review_request",
        ),
        sa.CheckConstraint(
            "verdict IN ('acceptable', 'minor_issue', "
            "'severe_error', 'insufficient_evidence')",
            name="ck_hidden_review_verdict",
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR reason_code IN "
            "('wrong_fact', 'privacy', 'unsafe_action', "
            "'missing_handoff', 'other')",
            name="ck_hidden_review_reason",
        ),
    )
    op.create_index(
        "ix_hidden_review_protocol", "api_hidden_shadow_case_reviews",
        ["protocol_id", "candidate_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text(
        "SELECT 1 FROM api_hidden_shadow_case_reviews LIMIT 1"
    )).first() is not None:
        raise RuntimeError(
            "Hidden shadow reviews exist; archive them before downgrade"
        )
    op.drop_index(
        "ix_hidden_review_protocol",
        table_name="api_hidden_shadow_case_reviews",
    )
    op.drop_table("api_hidden_shadow_case_reviews")
