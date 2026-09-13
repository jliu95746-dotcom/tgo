"""Add content snapshots without rewriting existing knowledge or bindings."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '72097dc45151'
down_revision = '62097dc45150'
branch_labels = None
depends_on = None


def timestamps():
    return [sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.create_table('rag_knowledge_version_sources',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('project_id', pg.UUID(as_uuid=True), nullable=False),
        sa.Column('collection_id', pg.UUID(as_uuid=True), sa.ForeignKey('rag_collections.id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_kind', sa.String(16), nullable=False),
        sa.Column('source_id', pg.UUID(as_uuid=True), nullable=False),
        sa.Column('active_file_id', pg.UUID(as_uuid=True)),
        sa.Column('active_number', sa.Integer()),
        sa.Column('disabled', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('retention', sa.Integer(), nullable=False, server_default='10'),
        *timestamps(),
        sa.UniqueConstraint('project_id','source_kind','source_id', name='uq_rag_version_source'),
        sa.CheckConstraint("source_kind IN ('file','qa','website')", name='ck_rag_version_source_kind'),
        sa.CheckConstraint('retention BETWEEN 2 AND 100', name='ck_rag_version_retention'))
    op.create_table('rag_knowledge_versions',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('source_id', pg.UUID(as_uuid=True), sa.ForeignKey('rag_knowledge_version_sources.id', ondelete='CASCADE'), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('state', sa.String(32), nullable=False),
        sa.Column('author', sa.String(255), nullable=False),
        sa.Column('requested_action', sa.String(16), nullable=False),
        sa.Column('digest', sa.String(64)),
        sa.Column('snapshot', pg.JSONB(), nullable=False),
        sa.Column('error', sa.Text()),
        sa.Column('published_by', sa.String(255)),
        sa.Column('published_at', sa.DateTime(timezone=True)),
        sa.Column('restored_from', sa.Integer()),
        *timestamps(),
        sa.UniqueConstraint('source_id','number', name='uq_rag_version_number'),
        sa.CheckConstraint('number > 0', name='ck_rag_version_number'),
        sa.CheckConstraint("state IN ('processing','ready','pending_review','published','retired','failed','unchanged')", name='ck_rag_version_state'),
        sa.CheckConstraint("requested_action IN ('save','submit','publish')", name='ck_rag_version_action'))


def downgrade() -> None:
    op.drop_table('rag_knowledge_versions')
    op.drop_table('rag_knowledge_version_sources')
