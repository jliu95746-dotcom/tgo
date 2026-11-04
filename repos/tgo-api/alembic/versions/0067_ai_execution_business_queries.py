"""Link customer AI attempts to formal business query audits without payloads.

Revision ID: 0067_ai_execution_business_queries
Revises: 0066_customer_connection_tests
"""

from alembic import op
import sqlalchemy as sa

revision = "0067_ai_execution_business_queries"
down_revision = "0066_customer_connection_tests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_ai_execution_business_queries",
        sa.Column("query_id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["api_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["execution_id"],
                                ["api_ai_execution_attempts.execution_id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "operation IN ('order_query','logistics_query',"
            "'customer_profile_query','membership_query')",
            name="ck_ai_business_query_operation",
        ),
        sa.CheckConstraint("outcome IN ('passed','failed')", name="ck_ai_business_query_outcome"),
        sa.UniqueConstraint("execution_id", "query_id", name="uq_ai_execution_business_query"),
    )
    op.create_index("ix_ai_business_query_project_execution",
                    "api_ai_execution_business_queries", ["project_id", "execution_id"])


def downgrade() -> None:
    if op.get_bind().execute(
        sa.text("SELECT 1 FROM api_ai_execution_business_queries LIMIT 1")
    ).first() is not None:
        raise RuntimeError("AI business query links exist; archive them before downgrade")
    op.drop_index("ix_ai_business_query_project_execution",
                  table_name="api_ai_execution_business_queries")
    op.drop_table("api_ai_execution_business_queries")
