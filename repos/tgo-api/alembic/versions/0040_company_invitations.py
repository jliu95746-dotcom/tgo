"""Add seat-reserving, revocable company invitations."""

from alembic import op
import sqlalchemy as sa

revision = "0040_company_invitations"
down_revision = "0039_company_email_trial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_company_invitations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("api_projects.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "staff_id",
            sa.Uuid(),
            sa.ForeignKey("api_staff.id"),
            nullable=False,
        ),
        sa.Column(
            "action_id",
            sa.Uuid(),
            sa.ForeignKey("api_email_actions.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("email", sa.String(50), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "role IN ('admin','user')", name="ck_invitation_role"
        ),
        sa.CheckConstraint(
            "status IN ('pending','accepted','revoked')",
            name="ck_invitation_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("api_company_invitations")
