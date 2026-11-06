"""Serialize channel transport without holding business row locks over HTTP."""

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import sys
from threading import Lock
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

_local_locks: dict[int, Lock] = {}


@contextmanager
def channel_transport_session(
    db: Session,
    project_id: UUID,
    visitor_id: UUID,
) -> Iterator[Session | None]:
    """Use one dedicated connection for the lock and short DB phases."""
    digest = hashlib.sha256(
        b"staff-channel-transport:" + project_id.bytes + visitor_id.bytes
    ).digest()
    key = int.from_bytes(digest[:8], "big", signed=True)
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite is used only for single-process unit tests, not production.
        lock = _local_locks.setdefault(key, Lock())
        acquired = lock.acquire(blocking=False)
        try:
            if not acquired:
                yield None
            else:
                with Session(bind=bind) as work:
                    yield work
        finally:
            if acquired:
                lock.release()
        return
    engine = bind if isinstance(bind, Engine) else bind.engine
    with engine.connect() as connection:
        acquired = bool(
            connection.execute(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": key},
            ).scalar_one()
        )
        connection.commit()
        try:
            if not acquired:
                yield None
            else:
                with Session(bind=connection) as work:
                    yield work
        finally:
            unwinding = sys.exc_info()[0] is not None
            if acquired:
                try:
                    unlocked = connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"),
                        {"key": key},
                    ).scalar_one()
                    if not unlocked:
                        raise RuntimeError("Channel transport lock was lost")
                    connection.commit()
                except BaseException:
                    connection.invalidate()
                    if not unwinding:
                        raise
