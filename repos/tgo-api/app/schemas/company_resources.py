"""Private service capacity contract, independent from the AI reply ledger."""

from datetime import datetime
from pydantic import BaseModel


class CompanyResources(BaseModel):
    metered: bool
    knowledge_bytes: int | None
    channel_limit: int | None
    expires_at: datetime | None
