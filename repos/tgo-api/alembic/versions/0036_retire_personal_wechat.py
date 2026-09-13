"""Retire personal WeChat without deleting visitor or conversation history.

Revision ID: 0036_retire_personal_wechat
Revises: 0035_delivery_training_context
"""

from alembic import op
import sqlalchemy as sa

revision = "0036_retire_personal_wechat"
down_revision = "0035_delivery_training_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep configuration and foreign-key targets for historical conversations.
    # Mark for normal HTTP synchronization; never write another service's DB.
    op.execute(sa.text(
        "UPDATE api_platforms SET is_active = false, ai_mode = 'off', "
        "sync_status = 'pending', sync_retry_count = 0, updated_at = CURRENT_TIMESTAMP "
        "WHERE type = 'wechat_personal'"
    ))
    op.execute(sa.text(
        "UPDATE api_platform_types SET is_supported = false "
        "WHERE type = 'wechat_personal'"
    ))


def downgrade() -> None:
    op.execute(sa.text(
        "UPDATE api_platform_types SET is_supported = true "
        "WHERE type = 'wechat_personal'"
    ))
    # Do not silently resume customer messaging on rollback. Existing records
    # remain intact and an administrator can explicitly re-enable them.
