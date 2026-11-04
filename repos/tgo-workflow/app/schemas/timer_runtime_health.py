"""Recent queue and database evidence, separate from desired plan state."""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel


class TimerRuntimeState(BaseModel):
    status: Literal[
        "disabled", "key_missing", "unavailable", "invalid_probe",
        "scheduler_missing", "worker_waiting", "database_unavailable", "ready",
    ]
    checked_at: datetime
    capability_enabled: bool = True
    ready: bool = False
    scheduler_recent: bool = False
    worker_recent: bool = False
    database_ready: bool = False
    scheduler_seen_at: datetime | None = None
    worker_seen_at: datetime | None = None
