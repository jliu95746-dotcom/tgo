"""Keep visitor access and immutable customer ownership separate from public IDs."""

from alembic import op
import sqlalchemy as sa

revision = "0051_visitor_business_scope"
down_revision = "0050_business_customer_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_visitors", sa.Column("access_secret_digest", sa.String(64), nullable=True)
    )
    op.add_column(
        "api_visitors",
        sa.Column("access_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "api_visitors",
        sa.Column("access_retired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "api_visitor_business_scopes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("api_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "visitor_id",
            sa.Uuid(),
            sa.ForeignKey("api_visitors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            sa.Uuid(),
            sa.ForeignKey("api_business_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_fingerprint", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "project_id",
            "visitor_id",
            "connection_id",
            name="uq_visitor_business_scope_owner",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_visitor_business_scope_revision"),
    )
    for column in ("project_id", "visitor_id", "connection_id"):
        op.create_index(
            "ix_api_visitor_business_scopes_" + column,
            "api_visitor_business_scopes",
            [column],
        )


def downgrade() -> None:
    db = op.get_bind()
    if db.scalar(
        sa.text("SELECT count(*) FROM api_visitor_business_scopes")
    ) or db.scalar(
        sa.text(
            "SELECT count(*) FROM api_visitors WHERE access_secret_digest IS NOT NULL OR access_retired_at IS NOT NULL"
        )
    ):
        raise RuntimeError(
            "访客访问和业务归属屏障非空，不能删除保护记录；请保留兼容代码回退"
        )
    op.drop_table("api_visitor_business_scopes")
    for column in ("access_retired_at", "access_revision", "access_secret_digest"):
        op.drop_column("api_visitors", column)
