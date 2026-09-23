"""Durable AI reservations and quota movements."""
from alembic import op

revision = "0042_ai_usage_ledger"
down_revision = "0041_billing_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE api_ai_usage_reservations (
	id UUID NOT NULL, 
	project_id UUID NOT NULL, 
	round_key VARCHAR(160) NOT NULL, 
	batch_id UUID NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	lease_id UUID NOT NULL, 
	attempt INTEGER NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	receipt JSON, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	settled_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_ai_usage_round UNIQUE (project_id, round_key), 
	CONSTRAINT ck_ai_usage_status CHECK (status IN ('reserved','publishing','settled','released','review')), 
	FOREIGN KEY(project_id) REFERENCES api_projects (id), 
	FOREIGN KEY(batch_id) REFERENCES api_ai_credit_batches (id)
)
""")
    op.execute('CREATE INDEX ix_api_ai_usage_reservations_project_id ON api_ai_usage_reservations (project_id)')
    op.execute("""
CREATE TABLE api_ai_usage_movements (
	id UUID NOT NULL, 
	project_id UUID NOT NULL, 
	reservation_id UUID NOT NULL, 
	batch_id UUID NOT NULL, 
	business_key VARCHAR(160) NOT NULL, 
	kind VARCHAR(24) NOT NULL, 
	amount INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES api_projects (id), 
	FOREIGN KEY(reservation_id) REFERENCES api_ai_usage_reservations (id), 
	FOREIGN KEY(batch_id) REFERENCES api_ai_credit_batches (id), 
	UNIQUE (business_key)
)
""")
    op.execute('CREATE INDEX ix_api_ai_usage_movements_project_id ON api_ai_usage_movements (project_id)')


def downgrade() -> None:
    op.drop_table('api_ai_usage_movements')
    op.drop_table('api_ai_usage_reservations')
