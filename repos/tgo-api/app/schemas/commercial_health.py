"""Public operator-only operational snapshot, without customer identifiers."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class CommercialHealthReport(BaseModel):
    checked_at: datetime
    status: Literal["clear", "attention"]
    counts: dict[str, int]
    will_change_data: Literal[False] = False
