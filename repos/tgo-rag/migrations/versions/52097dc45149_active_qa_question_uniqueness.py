"""Only active QA pairs participate in per-collection question uniqueness."""

from alembic import op
import sqlalchemy as sa

revision = "52097dc45149"
down_revision = "42097dc45148"
branch_labels = None
depends_on = None

INDEX = "idx_qa_pairs_collection_question"
TABLE = "rag_qa_pairs"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.drop_index(INDEX, table_name=TABLE)
    op.create_index(INDEX, TABLE, ["collection_id", "question_hash"],
                    unique=True, postgresql_where=sa.text("deleted_at IS NULL"))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute("LOCK TABLE rag_qa_pairs IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM rag_qa_pairs GROUP BY collection_id, question_hash
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION 'Cannot downgrade QA uniqueness: %',
                    'deleted history shares question hashes. No data was changed.';
            END IF;
        END $$
    """)
    op.drop_index(INDEX, table_name=TABLE)
    op.create_index(INDEX, TABLE, ["collection_id", "question_hash"], unique=True)
