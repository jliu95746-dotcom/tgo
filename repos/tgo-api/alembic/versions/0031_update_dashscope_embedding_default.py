"""Update the DashScope default embedding model.

Revision ID: 0031_qwen37_embedding
Revises: 0030_customer_logistics
"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0031_qwen37_embedding"
down_revision: Union[str, None] = "0030_customer_logistics"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def _replace_dashscope_embedding_model(source: str, target: str) -> None:
    connection = op.get_bind()
    table = sa.table(
        "api_ai_provider_default_models",
        sa.column("provider", sa.String(length=50)),
        sa.column("model_id", sa.String(length=100)),
        sa.column("model_name", sa.String(length=100)),
        sa.column("updated_at", sa.DateTime()),
    )

    target_exists = connection.execute(
        sa.select(sa.literal(1))
        .select_from(table)
        .where(
            table.c.provider == "dashscope",
            table.c.model_id == target,
        )
        .limit(1)
    ).scalar_one_or_none()

    if target_exists is not None:
        connection.execute(
            table.delete().where(
                table.c.provider == "dashscope",
                table.c.model_id == source,
            )
        )
        return

    connection.execute(
        table.update()
        .where(
            table.c.provider == "dashscope",
            table.c.model_id == source,
        )
        .values(
            model_id=target,
            model_name=target,
            updated_at=sa.func.now(),
        )
    )


def upgrade() -> None:
    _replace_dashscope_embedding_model(
        source="text-embedding-v4",
        target="qwen3.7-text-embedding",
    )


def downgrade() -> None:
    _replace_dashscope_embedding_model(
        source="qwen3.7-text-embedding",
        target="text-embedding-v4",
    )
