"""Pin new employee-workflow bindings to an exact published release.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
"""

from alembic import op
import sqlalchemy as sa

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, column_type in (
        ("release_version", sa.Integer()),
        ("release_id", sa.String(length=36)),
        ("release_digest", sa.String(length=64)),
    ):
        op.add_column(
            "ai_agent_workflows", sa.Column(name, column_type, nullable=True)
        )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM ai_agent_workflows "
                "WHERE release_version IS NOT NULL)")
    ):
        raise RuntimeError("已有固定工作流关联，先受控导出再回退")
    for name in ("release_digest", "release_id", "release_version"):
        op.drop_column("ai_agent_workflows", name)
