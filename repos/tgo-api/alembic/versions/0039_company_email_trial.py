"""Add opt-in company trials, one-time email actions and a durable outbox."""

from alembic import op
import sqlalchemy as sa

revision = "0039_company_email_trial"
down_revision = "0038_staff_account_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_company_accounts",
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("api_projects.id"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("trial_granted", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("seat_limit", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('pending','trial','active','expired','suspended')",
            name="ck_company_account_status",
        ),
        sa.CheckConstraint("seat_limit >= 0", name="ck_company_seat_limit"),
    )
    op.create_table(
        "api_email_actions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("api_projects.id"),
            nullable=False,
        ),
        sa.Column(
            "staff_id",
            sa.Uuid(),
            sa.ForeignKey("api_staff.id"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "purpose IN ('verify','reset','invite')",
            name="ck_email_action_purpose",
        ),
    )
    op.create_table(
        "api_email_outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("api_projects.id"),
            nullable=False,
        ),
        sa.Column(
            "action_id",
            sa.Uuid(),
            sa.ForeignKey("api_email_actions.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("lease_id", sa.Uuid()),
        sa.Column("last_error", sa.String(100)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "api_ai_credit_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("api_projects.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("source_key", sa.String(160), nullable=False, unique=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("remaining", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("amount >= 0", name="ck_credit_amount"),
        sa.CheckConstraint("remaining >= 0", name="ck_credit_remaining"),
        sa.CheckConstraint("remaining <= amount", name="ck_credit_balance"),
    )


def downgrade() -> None:
    for table in (
        "api_ai_credit_batches",
        "api_email_outbox",
        "api_email_actions",
        "api_company_accounts",
    ):
        op.drop_table(table)
