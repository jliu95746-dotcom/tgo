"""Company-owned revisioned connections and scope-bound credential ciphertext."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0048_business_connections"
down_revision = "0047_scenario_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_business_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("api_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by", sa.Uuid(), sa.ForeignKey("api_staff.id", ondelete="SET NULL")
        ),
        sa.Column(
            "updated_by", sa.Uuid(), sa.ForeignKey("api_staff.id", ondelete="SET NULL")
        ),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(), nullable=False),
        sa.Column("configuration", postgresql.JSONB()),
        sa.Column("credential_ciphertext", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("configuration_revision", sa.Integer(), nullable=False),
        sa.Column("authorization_revision", sa.Integer(), nullable=False),
        sa.Column("verified_configuration_revision", sa.Integer()),
        sa.Column("verified_capabilities", postgresql.JSONB(), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True)),
        sa.Column(
            "last_tested_by",
            sa.Uuid(),
            sa.ForeignKey("api_staff.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "project_id", "request_id", name="uq_business_connection_request"
        ),
        sa.CheckConstraint(
            "revision >= 1 AND configuration_revision >= 1 AND authorization_revision >= 1",
            name="ck_business_connection_revisions",
        ),
        sa.CheckConstraint(
            "mode IN ('http', 'demo')", name="ck_business_connection_mode"
        ),
    )
    op.create_index(
        "ix_api_business_connections_project_id",
        "api_business_connections",
        ["project_id"],
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS(SELECT 1 FROM api_business_connections)"))
        .scalar()
    ):
        raise RuntimeError(
            "Existing business connections must be retained; roll back consumers without dropping saved configuration"
        )
    op.drop_table("api_business_connections")
