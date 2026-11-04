"""Freeze independent G4 populations separately from staff-visible drafts.

Revision ID: 0069_hidden_shadow_protocols
Revises: 0068_hidden_shadow_runs
"""

import sqlalchemy as sa
from alembic import op

revision = "0069_hidden_shadow_protocols"
down_revision = "0068_hidden_shadow_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_hidden_shadow_protocols",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("algorithm", sa.String(40), nullable=False),
        sa.Column("random_seed", sa.String(32), nullable=False),
        sa.Column("population_count", sa.Integer()),
        sa.Column("sample_count", sa.Integer()),
        sa.Column("population_sha256", sa.String(64)),
        sa.Column("manifest_sha256", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["project_id"], ["api_projects.id"], ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "project_id", "request_id", name="uq_hidden_protocol_request",
        ),
        sa.CheckConstraint(
            "window_start < window_end", name="ck_hidden_protocol_window",
        ),
        sa.CheckConstraint(
            "sample_size BETWEEN 1 AND 500", name="ck_hidden_protocol_size",
        ),
        sa.CheckConstraint(
            "(finalized_at IS NULL AND population_count IS NULL AND "
            "sample_count IS NULL AND population_sha256 IS NULL AND "
            "manifest_sha256 IS NULL) OR "
            "(finalized_at IS NOT NULL AND population_count >= 0 AND "
            "sample_count >= 0 AND sample_count <= population_count AND "
            "sample_count <= sample_size AND population_sha256 IS NOT NULL "
            "AND manifest_sha256 IS NOT NULL)",
            name="ck_hidden_protocol_outcome",
        ),
    )
    op.create_index(
        "uq_hidden_protocol_active_project", "api_hidden_shadow_protocols",
        ["project_id"], unique=True,
        postgresql_where=sa.text("finalized_at IS NULL"),
        sqlite_where=sa.text("finalized_at IS NULL"),
    )
    op.create_table(
        "api_hidden_shadow_protocol_cases",
        sa.Column("protocol_id", sa.Uuid(), primary_key=True),
        sa.Column("candidate_id", sa.Uuid(), primary_key=True),
        sa.Column("visitor_id", sa.Uuid(), nullable=False),
        sa.Column("staff_delivery_id", sa.Uuid(), nullable=False),
        sa.Column("source_message_id", sa.String(255), nullable=False),
        sa.Column("source_content_sha256", sa.String(64), nullable=False),
        sa.Column("candidate_sha256", sa.String(64), nullable=False),
        sa.Column("staff_reply_sha256", sa.String(64), nullable=False),
        sa.Column("candidate_completed_at", sa.DateTime(timezone=True),
                  nullable=False),
        sa.Column("staff_reply_created_at", sa.DateTime(timezone=True),
                  nullable=False),
        sa.Column("selected_position", sa.Integer()),
        sa.ForeignKeyConstraint(
            ["protocol_id"], ["api_hidden_shadow_protocols.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["api_hidden_shadow_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["staff_delivery_id"], ["api_staff_message_deliveries.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "protocol_id", "staff_delivery_id",
            name="uq_hidden_protocol_delivery",
        ),
        sa.UniqueConstraint(
            "protocol_id", "selected_position",
            name="uq_hidden_protocol_position",
        ),
        sa.CheckConstraint(
            "selected_position IS NULL OR selected_position > 0",
            name="ck_hidden_protocol_position",
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    for table_name in (
        "api_hidden_shadow_protocol_cases",
        "api_hidden_shadow_protocols",
    ):
        if connection.execute(
            sa.text(f"SELECT 1 FROM {table_name} LIMIT 1")
        ).first() is not None:
            raise RuntimeError(
                "Hidden shadow protocols exist; archive them before downgrade"
            )
    op.drop_table("api_hidden_shadow_protocol_cases")
    op.drop_index(
        "uq_hidden_protocol_active_project",
        table_name="api_hidden_shadow_protocols",
    )
    op.drop_table("api_hidden_shadow_protocols")
