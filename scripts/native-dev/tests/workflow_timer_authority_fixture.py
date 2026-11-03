"""Seed or revoke a synthetic administrator in an owned disposable database."""

import argparse
import logging
import os
from pathlib import Path
import re
import sys
from uuid import UUID

from sqlalchemy.engine import make_url

logging.disable(logging.CRITICAL)
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "repos/tgo-api"))
from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, sync_engine  # noqa: E402
from app.models import Project, Staff  # noqa: E402


def main(action: str) -> None:
    target = os.environ["TGO_TIMER_TEST_DATABASE"]
    url = make_url(settings.database_url_sync)
    if (
        not __debug__
        or not re.fullmatch(r"tgo_scenarios_test_timer_[0-9a-f]{32}", target)
        or url.database != target
        or url.host not in {"127.0.0.1", "localhost", "::1"}
    ):
        raise RuntimeError("An owned disposable loopback database is required")
    project = UUID(os.environ["TGO_TIMER_TEST_PROJECT"])
    actor = UUID(os.environ["TGO_TIMER_TEST_ACTOR"])
    name = "timer-runtime-" + project.hex
    if action == "seed":
        Base.metadata.create_all(
            sync_engine, tables=[Project.__table__, Staff.__table__]
        )
        with SessionLocal() as db:
            if db.get(Project, project) is not None:
                raise RuntimeError("The synthetic project already exists")
            db.add(Project(id=project, name=name, api_key=settings.SECRET_KEY))
            db.flush()
            db.add(
                Staff(
                    id=actor,
                    project_id=project,
                    role="admin",
                    account_enabled=True,
                    username="timer-" + project.hex[:24] + "@example.test",
                    password_hash="disabled-synthetic-login",
                    token_version=1,
                )
            )
            db.commit()
    else:
        with SessionLocal() as db:
            owner = db.get(Project, project)
            staff = db.get(Staff, actor)
            if (
                owner is None
                or owner.name != name
                or owner.api_key != settings.SECRET_KEY
                or staff is None
                or staff.project_id != project
            ):
                raise RuntimeError("Synthetic project ownership mismatch")
            staff.role = "user" if action == "revoke" else "admin"
            db.commit()
    sync_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["seed", "revoke", "grant"])
    main(parser.parse_args().action)
