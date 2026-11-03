"""Employee business selections and durable original configuration receipts."""
from alembic import op
import sqlalchemy as sa

revision = "y7z8a9b0c1d2"
down_revision = "x6y7z8a9b0c1"
branch_labels = None
depends_on = None


def common():
    return [sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("project_id", sa.Uuid(), nullable=False),
            sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("ai_agents.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)]


def upgrade():
    op.create_table("ai_agent_business_connections", *common(),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("operations", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("configuration_revision", sa.Integer(), nullable=False),
        sa.Column("authorization_revision", sa.Integer(), nullable=False),
        sa.Column("resource_revision", sa.String(64), nullable=False),
        sa.Column("configured_by", sa.Uuid(), nullable=False),
        sa.UniqueConstraint("project_id", "agent_id", "connection_id", name="uq_ai_employee_connection"),
        sa.CheckConstraint("revision >= 1 AND configuration_revision >= 1 AND authorization_revision >= 1",
                           name="ck_ai_employee_connection_revisions"))
    op.create_table("ai_agent_business_connection_requests", *common(),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("command_digest", sa.String(64), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("project_id", "requested_by", "request_id", name="uq_ai_employee_connection_request"))
    for table in ("ai_agent_business_connections", "ai_agent_business_connection_requests"):
        for column in ("project_id", "agent_id"):
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    connection = op.get_bind()
    tables = ("ai_agent_business_connections", "ai_agent_business_connection_requests")
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE ai_agent_business_connections, ai_agent_business_connection_requests IN ACCESS EXCLUSIVE MODE"))
    if any(connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar() for table in tables):
        raise RuntimeError("Employee business records exist; use a compatible rollback")
    for table in reversed(tables):
        op.drop_table(table)
