"""Reuse owned staff seeding; revoke login rather than a still-valid role."""

import argparse
import importlib.util
from pathlib import Path
import os
from uuid import UUID


def main(action):
    spec = importlib.util.spec_from_file_location(
        "approval_key_staff_fixture",
        Path(__file__).with_name("workflow_timer_authority_fixture.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # This also verifies loopback database, synthetic ownership and exact IDs.
    module.main("seed" if action == "seed" else "grant")
    if action == "disable":
        with module.SessionLocal() as db:
            staff = db.get(module.Staff, UUID(os.environ["TGO_TIMER_TEST_ACTOR"]))
            assert staff.project_id == UUID(os.environ["TGO_TIMER_TEST_PROJECT"])
            staff.account_enabled = False
            db.commit()
        module.sync_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["seed", "disable"])
    main(parser.parse_args().action)
