"""Freeze legacy active definitions and add immutable releases.

Revision ID: 0003_workflow_releases
Revises: 0002_execution_snapshots
"""

import hashlib
import json
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "0003_workflow_releases"
down_revision = "0002_execution_snapshots"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "wf_workflows",
        sa.Column("published_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "wf_workflows",
        sa.Column(
            "publication_revision",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_table(
        "wf_workflow_releases",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "workflow_id",
            sa.String(),
            sa.ForeignKey("wf_workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("draft_version", sa.Integer(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("definition_digest", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workflow_id", "version", name="uq_workflow_release_version"
        ),
    )
    op.create_index(
        "ix_wf_workflow_releases_workflow_id",
        "wf_workflow_releases",
        ["workflow_id"],
    )
    op.create_index(
        "ix_wf_workflow_releases_project_id",
        "wf_workflow_releases",
        ["project_id"],
    )
    op.add_column(
        "wf_workflow_executions",
        sa.Column("release_id", sa.String(), nullable=True),
    )
    # Freeze exact legacy active definitions; do not infer publication from a draft.
    bind = op.get_bind()
    workflow = sa.table(
        "wf_workflows",
        sa.column("id"),
        sa.column("project_id"),
        sa.column("definition", sa.JSON()),
        sa.column("version"),
        sa.column("status"),
        sa.column("created_by"),
        sa.column("published_version"),
        sa.column("publication_revision"),
    )
    release = sa.table(
        "wf_workflow_releases",
        sa.column("id"),
        sa.column("workflow_id"),
        sa.column("project_id"),
        sa.column("version"),
        sa.column("draft_version"),
        sa.column("definition", sa.JSON()),
        sa.column("definition_digest"),
        sa.column("created_by"),
    )
    for row in bind.execute(
        sa.select(workflow).where(workflow.c.status == "active")
    ).mappings():
        encoded = json.dumps(
            row["definition"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        bind.execute(
            release.insert().values(
                id=str(uuid4()),
                workflow_id=row["id"],
                project_id=row["project_id"],
                version=1,
                draft_version=row["version"],
                definition=row["definition"],
                definition_digest=hashlib.sha256(encoded).hexdigest(),
                created_by=row["created_by"],
            )
        )
        bind.execute(
            workflow.update()
            .where(workflow.c.id == row["id"])
            .values(published_version=1, publication_revision=1)
        )


def downgrade():
    op.drop_column("wf_workflow_executions", "release_id")
    op.drop_index("ix_wf_workflow_releases_project_id", "wf_workflow_releases")
    op.drop_index(
        "ix_wf_workflow_releases_workflow_id", "wf_workflow_releases"
    )
    op.drop_table("wf_workflow_releases")
    op.drop_column("wf_workflows", "publication_revision")
    op.drop_column("wf_workflows", "published_version")
