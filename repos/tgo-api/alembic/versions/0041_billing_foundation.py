"""Persist immutable quotes, payments, subscription periods and operations."""
from alembic import op
import sqlalchemy as sa

revision = "0041_billing_foundation"
down_revision = "0040_company_invitations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE api_billing_plans (
	id UUID NOT NULL, 
	code VARCHAR(40) NOT NULL, 
	version INTEGER NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	definition JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_billing_plan_version UNIQUE (code, version)
)
""")
    op.execute('CREATE INDEX ix_api_billing_plans_code ON api_billing_plans (code)')
    op.execute("""
CREATE TABLE api_billing_quotes (
	id UUID NOT NULL, 
	project_id UUID NOT NULL, 
	staff_id UUID NOT NULL, 
	base_version INTEGER NOT NULL, 
	amount INTEGER NOT NULL, 
	details JSON NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES api_projects (id), 
	FOREIGN KEY(staff_id) REFERENCES api_staff (id)
)
""")
    op.execute('CREATE INDEX ix_api_billing_quotes_project_id ON api_billing_quotes (project_id)')
    op.execute("""
CREATE TABLE api_billing_orders (
	id UUID NOT NULL, 
	number VARCHAR(32) NOT NULL, 
	project_id UUID NOT NULL, 
	quote_id UUID NOT NULL, 
	amount INTEGER NOT NULL, 
	refunded_amount INTEGER NOT NULL, 
	payment_status VARCHAR(24) NOT NULL, 
	fulfillment_status VARCHAR(24) NOT NULL, 
	transaction_id VARCHAR(64), 
	code_url VARCHAR(1024), 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	paid_at TIMESTAMP WITH TIME ZONE, 
	fulfilled_at TIMESTAMP WITH TIME ZONE, 
	failure_code VARCHAR(100), 
	last_checked_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_order_amount CHECK (amount >= 0), 
	CONSTRAINT ck_order_refunded CHECK (refunded_amount >= 0 AND refunded_amount <= amount), 
	UNIQUE (number), 
	FOREIGN KEY(project_id) REFERENCES api_projects (id), 
	UNIQUE (quote_id), 
	FOREIGN KEY(quote_id) REFERENCES api_billing_quotes (id), 
	UNIQUE (transaction_id)
)
""")
    op.execute('CREATE INDEX ix_api_billing_orders_project_id ON api_billing_orders (project_id)')
    op.execute("""
CREATE TABLE api_subscription_periods (
	id UUID NOT NULL, 
	project_id UUID NOT NULL, 
	order_id UUID NOT NULL, 
	plan_id UUID NOT NULL, 
	starts_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	ends_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	months INTEGER NOT NULL, 
	anchor_day INTEGER NOT NULL, 
	original_price INTEGER NOT NULL, 
	current_price INTEGER NOT NULL, 
	original_definition JSON NOT NULL, 
	definition JSON NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES api_projects (id), 
	UNIQUE (order_id), 
	FOREIGN KEY(order_id) REFERENCES api_billing_orders (id), 
	FOREIGN KEY(plan_id) REFERENCES api_billing_plans (id)
)
""")
    op.execute('CREATE INDEX ix_api_subscription_periods_project_id ON api_subscription_periods (project_id)')
    op.execute("""
CREATE TABLE api_seat_addons (
	id UUID NOT NULL, 
	project_id UUID NOT NULL, 
	order_id UUID NOT NULL, 
	quantity INTEGER NOT NULL, 
	starts_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	ends_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES api_projects (id), 
	UNIQUE (order_id), 
	FOREIGN KEY(order_id) REFERENCES api_billing_orders (id)
)
""")
    op.execute('CREATE INDEX ix_api_seat_addons_project_id ON api_seat_addons (project_id)')
    op.execute("""
CREATE TABLE api_payment_events (
	id UUID NOT NULL, 
	event_key VARCHAR(160) NOT NULL, 
	project_id UUID NOT NULL, 
	order_id UUID NOT NULL, 
	transaction_id VARCHAR(64) NOT NULL, 
	amount INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (event_key), 
	FOREIGN KEY(project_id) REFERENCES api_projects (id), 
	FOREIGN KEY(order_id) REFERENCES api_billing_orders (id)
)
""")
    op.execute('CREATE INDEX ix_api_payment_events_project_id ON api_payment_events (project_id)')
    op.execute("""
CREATE TABLE api_billing_jobs (
	id UUID NOT NULL, 
	business_key VARCHAR(160) NOT NULL, 
	project_id UUID, 
	order_id UUID, 
	kind VARCHAR(24) NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	attempts INTEGER NOT NULL, 
	available_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	locked_until TIMESTAMP WITH TIME ZONE, 
	lease_id UUID, 
	last_error VARCHAR(100), 
	PRIMARY KEY (id), 
	UNIQUE (business_key), 
	FOREIGN KEY(project_id) REFERENCES api_projects (id), 
	FOREIGN KEY(order_id) REFERENCES api_billing_orders (id)
)
""")
    op.execute('CREATE INDEX ix_api_billing_jobs_project_id ON api_billing_jobs (project_id)')
    op.execute("""
CREATE TABLE api_billing_refunds (
	id UUID NOT NULL, 
	number VARCHAR(32) NOT NULL, 
	project_id UUID NOT NULL, 
	order_id UUID NOT NULL, 
	operator_id UUID NOT NULL, 
	amount INTEGER NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	reason VARCHAR(500) NOT NULL, 
	disposition JSON NOT NULL, 
	provider_id VARCHAR(64), 
	succeeded_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (number), 
	FOREIGN KEY(project_id) REFERENCES api_projects (id), 
	FOREIGN KEY(order_id) REFERENCES api_billing_orders (id), 
	FOREIGN KEY(operator_id) REFERENCES api_platform_operators (id), 
	UNIQUE (provider_id)
)
""")
    op.execute('CREATE INDEX ix_api_billing_refunds_project_id ON api_billing_refunds (project_id)')
    op.execute("""
CREATE TABLE api_billing_audits (
	id UUID NOT NULL, 
	operator_id UUID NOT NULL, 
	project_id UUID, 
	action VARCHAR(60) NOT NULL, 
	reason VARCHAR(500) NOT NULL, 
	detail JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(operator_id) REFERENCES api_platform_operators (id), 
	FOREIGN KEY(project_id) REFERENCES api_projects (id)
)
""")
    op.execute('CREATE INDEX ix_api_billing_audits_project_id ON api_billing_audits (project_id)')
    op.execute("""
CREATE TABLE api_invoice_requests (
	id UUID NOT NULL, 
	project_id UUID NOT NULL, 
	order_id UUID NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	tax_number VARCHAR(40) NOT NULL, 
	email VARCHAR(254) NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	invoice_number VARCHAR(100), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES api_projects (id), 
	UNIQUE (order_id), 
	FOREIGN KEY(order_id) REFERENCES api_billing_orders (id)
)
""")
    op.execute('CREATE INDEX ix_api_invoice_requests_project_id ON api_invoice_requests (project_id)')
    op.add_column("api_company_accounts", sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("api_billing_plans.id")))
    op.add_column("api_company_accounts", sa.Column("billing_months", sa.Integer()))
    op.add_column("api_company_accounts", sa.Column("anchor_day", sa.Integer()))
    op.add_column("api_ai_credit_batches", sa.Column("order_id", sa.Uuid(), sa.ForeignKey("api_billing_orders.id")))


def downgrade() -> None:
    op.drop_column("api_ai_credit_batches", "order_id")
    op.drop_column("api_company_accounts", "anchor_day")
    op.drop_column("api_company_accounts", "billing_months")
    op.drop_column("api_company_accounts", "plan_id")
    op.drop_table('api_invoice_requests')
    op.drop_table('api_billing_audits')
    op.drop_table('api_billing_refunds')
    op.drop_table('api_billing_jobs')
    op.drop_table('api_payment_events')
    op.drop_table('api_seat_addons')
    op.drop_table('api_subscription_periods')
    op.drop_table('api_billing_orders')
    op.drop_table('api_billing_quotes')
    op.drop_table('api_billing_plans')
