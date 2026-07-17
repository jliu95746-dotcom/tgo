"""add knowledge governance

Revision ID: 42097dc45148
Revises: 32097dc45147
Create Date: 2026-07-16 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "42097dc45148"
down_revision = "32097dc45147"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rag_knowledge_governance",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("file_id", sa.UUID(), nullable=True),
        sa.Column("qa_pair_id", sa.UUID(), nullable=True),
        sa.Column("document_type", sa.String(length=32), nullable=False),
        sa.Column("product_line", sa.String(length=128), nullable=False),
        sa.Column(
            "channels",
            postgresql.ARRAY(sa.String(length=32)),
            nullable=False,
        ),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner", sa.String(length=255), nullable=False),
        sa.Column("document_version", sa.String(length=64), nullable=False),
        sa.Column(
            "allow_automatic_reply",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "review_status",
            sa.String(length=32),
            server_default="draft",
            nullable=False,
        ),
        sa.Column("reviewed_by", sa.String(length=255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "source_origin",
            sa.String(length=32),
            server_default="internal",
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "document_type IN ('product', 'after_sales', 'faq', 'sop')",
            name="ck_rag_knowledge_governance_document_type",
        ),
        sa.CheckConstraint(
            "num_nonnulls(file_id, qa_pair_id) = 1",
            name="ck_rag_knowledge_governance_exactly_one_source",
        ),
        sa.CheckConstraint(
            "review_status IN "
            "('draft', 'pending_review', 'approved', 'rejected', 'revoked')",
            name="ck_rag_knowledge_governance_review_status",
        ),
        sa.CheckConstraint(
            "review_status NOT IN ('approved', 'rejected', 'revoked') "
            "OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_rag_knowledge_governance_review_audit",
        ),
        sa.CheckConstraint(
            "source_origin IN ('internal', 'customer', 'website')",
            name="ck_rag_knowledge_governance_source_origin",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > effective_at",
            name="ck_rag_knowledge_governance_valid_window",
        ),
        sa.CheckConstraint(
            "cardinality(channels) BETWEEN 1 AND 16 "
            "AND channels <@ ARRAY["
            "'wecom_kf', 'web', 'app', 'phone', 'internal'"
            "]::varchar[]",
            name="ck_rag_knowledge_governance_channels",
        ),
        sa.CheckConstraint(
            "btrim(product_line) <> '' AND btrim(owner) <> '' "
            "AND btrim(document_version) <> ''",
            name="ck_rag_knowledge_governance_nonblank_metadata",
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["rag_files.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["qa_pair_id"],
            ["rag_qa_pairs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "file_id",
            name="uq_rag_knowledge_governance_file_id",
        ),
        sa.UniqueConstraint(
            "qa_pair_id",
            name="uq_rag_knowledge_governance_qa_pair_id",
        ),
    )
    op.create_index(
        "idx_rag_knowledge_governance_project_review",
        "rag_knowledge_governance",
        ["project_id", "review_status"],
        unique=False,
    )
    op.create_index(
        "idx_rag_knowledge_governance_validity",
        "rag_knowledge_governance",
        ["effective_at", "expires_at"],
        unique=False,
    )
    op.create_index(
        "idx_rag_knowledge_governance_channels",
        "rag_knowledge_governance",
        ["channels"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "idx_rag_knowledge_governance_deleted_at",
        "rag_knowledge_governance",
        ["deleted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_rag_knowledge_governance_deleted_at",
        table_name="rag_knowledge_governance",
    )
    op.drop_index(
        "idx_rag_knowledge_governance_channels",
        table_name="rag_knowledge_governance",
        postgresql_using="gin",
    )
    op.drop_index(
        "idx_rag_knowledge_governance_validity",
        table_name="rag_knowledge_governance",
    )
    op.drop_index(
        "idx_rag_knowledge_governance_project_review",
        table_name="rag_knowledge_governance",
    )
    op.drop_table("rag_knowledge_governance")
