"""Document the expression response-purpose runtime contract.

The contract lives in Pydantic integration models and does not add a database
column.  This no-op revision keeps the migration audit explicit when runtime
model contracts change.
"""

from typing import Sequence, Union

revision: str = "m5n6o7p8q9r0"
down_revision: Union[str, None] = "l4m5n6o7p8q9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No database change; response_purpose is an internal Pydantic field."""


def downgrade() -> None:
    """No database change to reverse."""
