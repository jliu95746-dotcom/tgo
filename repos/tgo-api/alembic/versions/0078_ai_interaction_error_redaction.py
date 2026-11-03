"""Remove legacy upstream exception text from AI interaction runs.

Revision ID: 0078_ai_interaction_error_redaction
Revises: 0077_visitor_im_disconnect_lease
"""

import sqlalchemy as sa
from alembic import op

revision = "0078_ai_interaction_error_redaction"
down_revision = "0077_visitor_im_disconnect_lease"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    known_legacy_messages = (
        ("AI stream cancelled before completion", "ai_stream_cancelled"),
        ("Background AI interaction was cancelled", "ai_background_cancelled"),
        ("客户业务会话无法确认，未执行查询", "business_context_unavailable"),
    )
    for previous, code in known_legacy_messages:
        connection.execute(sa.text(
            "UPDATE api_ai_interaction_runs SET error_message = :code "
            "WHERE error_message = :previous"
        ), {"previous": previous, "code": code})
    connection.execute(sa.text(
        "UPDATE api_ai_interaction_runs SET error_message = NULL "
        "WHERE error_message = ''"
    ))
    known_codes = (
        "ai_processing_failed", "ai_workflow_failed", "ai_stream_failed",
        "ai_stream_cancelled", "ai_background_cancelled",
        "business_context_unavailable", "visitor_access_ended",
    )
    statement = sa.text(
        "UPDATE api_ai_interaction_runs "
        "SET error_message = 'ai_processing_failed' "
        "WHERE error_message IS NOT NULL AND error_message NOT IN :known_codes"
    ).bindparams(sa.bindparam("known_codes", expanding=True))
    connection.execute(statement, {"known_codes": known_codes})


def downgrade() -> None:
    # Removed exception text cannot be reconstructed by a schema rollback.
    pass
