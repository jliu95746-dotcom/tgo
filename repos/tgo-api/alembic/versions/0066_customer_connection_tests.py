"""Allow read-only customer profile and membership configuration tests.

Revision ID: 0066_customer_connection_tests
Revises: 0065_ai_execution_tool_events
"""

from alembic import op
import sqlalchemy as sa

revision = "0066_customer_connection_tests"
down_revision = "0065_ai_execution_tool_events"
branch_labels = None
depends_on = None

_TABLE = "api_business_connection_tests"
_NAME = "ck_connection_test_operation"
_OLD = "operation IN ('order_query','logistics_query')"
_NEW = (
    "operation IN ('order_query','logistics_query',"
    "'customer_profile_query','membership_query')"
)


def upgrade() -> None:
    op.drop_constraint(_NAME, _TABLE, type_="check")
    op.create_check_constraint(_NAME, _TABLE, _NEW)


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM api_business_connection_tests "
            "WHERE operation IN ('customer_profile_query','membership_query'))"
        )
    ):
        raise RuntimeError("Customer query test reports exist; archive them before downgrade")
    op.drop_constraint(_NAME, _TABLE, type_="check")
    op.create_check_constraint(_NAME, _TABLE, _OLD)
