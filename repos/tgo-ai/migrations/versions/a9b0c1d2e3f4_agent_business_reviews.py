"""Independent business reviews preserve original decisions and request receipts."""

from alembic import op
import sqlalchemy as sa

revision = "a9b0c1d2e3f4"
down_revision = "z8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade():
    columns = [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "report_id",
            sa.Uuid(),
            sa.ForeignKey("ai_agent_business_trials.id"),
            nullable=False,
        ),
    ]
    columns += [
        sa.Column(name, sa.Uuid(), nullable=False)
        for name in (
            "project_id",
            "reviewed_by",
            "agent_id",
            "connection_id",
            "request_id",
        )
    ]
    columns += [
        sa.Column(name, sa.String(64), nullable=False)
        for name in ("request_digest", "report_digest", "configuration_fingerprint")
    ]
    columns += [
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    ]
    columns += [
        sa.Column(
            name,
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        )
        for name in ("created_at", "updated_at")
    ]
    op.create_table(
        "ai_agent_business_reviews",
        *columns,
        sa.UniqueConstraint(
            "project_id",
            "reviewed_by",
            "request_id",
            name="uq_ai_business_review_request",
        ),
        sa.UniqueConstraint(
            "project_id", "report_id", "revision", name="uq_ai_business_review_revision"
        ),
        sa.CheckConstraint(
            "decision IN ('passed', 'rejected')", name="ck_ai_business_review_decision"
        ),
    )
    for name in ("project_id", "agent_id", "report_id"):
        op.create_index(
            "ix_ai_agent_business_reviews_" + name, "ai_agent_business_reviews", [name]
        )


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text("LOCK TABLE ai_agent_business_reviews IN ACCESS EXCLUSIVE MODE")
        )
    if connection.execute(
        sa.text("SELECT count(*) FROM ai_agent_business_reviews")
    ).scalar():
        raise RuntimeError("Business reviews exist; use a compatible rollback")
    op.drop_table("ai_agent_business_reviews")
