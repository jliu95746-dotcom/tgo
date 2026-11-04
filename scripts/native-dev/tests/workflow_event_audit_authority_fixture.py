"""Seed actual API authority records only in an owned disposable database."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import re
import sys
from uuid import UUID, uuid4

from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "repos/tgo-api"))
from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, sync_engine  # noqa: E402
from app.models import (  # noqa: E402
    Project,
    Staff,
    Platform,
    Visitor,
    VisitorSession,
    SessionCommandDelivery,
)
from app.models.channel_memory_clearance import ChannelMemoryClearance  # noqa: E402
from app.models.workflow_event_subscription import (  # noqa: E402
    WorkflowEventDelivery,
    WorkflowEventSubscription,
)

logging.disable(logging.CRITICAL)


def main(action):
    database = os.environ["TGO_EVENT_AUDIT_DATABASE"]
    url = make_url(settings.database_url_sync)
    assert __debug__ and url.database == database
    assert re.fullmatch(r"tgo_scenarios_test_event_audit_[a-f0-9]{32}", database)
    assert url.host in {"localhost", "127.0.0.1", "::1"}
    project = UUID(os.environ["TGO_EVENT_AUDIT_PROJECT"])
    cases = json.loads(os.environ["TGO_EVENT_AUDIT_CASES"])
    assert len(cases) == 4
    assert {row["case"] for row in cases} == {
        "disabled",
        "rebound",
        "cleared",
        "staff_revoked",
    }
    name = "event-audit-" + project.hex
    output = Path(os.environ["TGO_EVENT_AUDIT_BINDINGS"]).resolve()
    expected = (ROOT / ".tmp/native-dev" / ("event-audit-" + database[-32:])).resolve()
    assert output.parent == expected and output.name == "source-bindings.json"
    if action == "seed":
        tables = {
            model.__table__
            for model in (
                Project,
                Staff,
                Platform,
                Visitor,
                VisitorSession,
                SessionCommandDelivery,
                ChannelMemoryClearance,
                WorkflowEventSubscription,
                WorkflowEventDelivery,
            )
        }
        pending = list(tables)
        while pending:
            table = pending.pop()
            for foreign in table.foreign_keys:
                target = foreign.column.table
                if target not in tables:
                    tables.add(target)
                    pending.append(target)
        Base.metadata.create_all(sync_engine, tables=list(tables))
        now = datetime.now(timezone.utc)
        version = now.replace(tzinfo=None)
        bindings = []
        with SessionLocal() as db:
            assert db.get(Project, project) is None
            db.add(Project(id=project, name=name, api_key=settings.SECRET_KEY))
            db.flush()
            for row in cases:
                actor, platform, visitor, session, command, sub, delivery = (
                    uuid4() for _ in range(7)
                )
                db.add(
                    Staff(
                        id=actor,
                        project_id=project,
                        role="admin",
                        account_enabled=True,
                        is_active=True,
                        username="audit-" + actor.hex[:24] + "@example.test",
                        password_hash="disabled-synthetic-login",
                        token_version=1,
                    )
                )
                db.add(
                    Platform(
                        id=platform,
                        project_id=project,
                        type="website",
                        name="合成来源渠道",
                        api_key="synthetic-" + platform.hex,
                        is_active=True,
                        config={"ai_enabled": False},
                    )
                )
                db.flush()
                db.add(
                    Visitor(
                        id=visitor,
                        project_id=project,
                        platform_id=platform,
                        platform_open_id="synthetic-" + visitor.hex,
                        name="合成来源访客",
                        ai_disabled=True,
                    )
                )
                db.flush()
                db.add(
                    VisitorSession(
                        id=session,
                        project_id=project,
                        visitor_id=visitor,
                        platform_id=platform,
                        staff_id=actor,
                        status="closed",
                        created_at=version - timedelta(minutes=5),
                        closed_at=version,
                        updated_at=version,
                        last_message_seq=50,
                    )
                )
                db.flush()
                db.add(
                    SessionCommandDelivery(
                        id=command,
                        project_id=project,
                        visitor_id=visitor,
                        session_id=session,
                        actor_staff_id=actor,
                        kind="close",
                        session_version=version,
                        actor_role="admin",
                        actor_token_version=1,
                        actor_token_expires_at=now + timedelta(hours=1),
                        client_msg_no=uuid4().hex,
                        channel_id=str(visitor),
                        channel_type=251,
                        from_uid="synthetic-actor",
                        payload={},
                    )
                )
                db.add(
                    WorkflowEventSubscription(
                        id=sub,
                        project_id=project,
                        workflow_id=row["workflow_id"],
                        enabled=True,
                        revision=1,
                        release_id=row["release_id"],
                        release_digest=row["release_digest"],
                        version=1,
                        enabled_at=now - timedelta(minutes=1),
                        enabled_by_staff_id=actor,
                    )
                )
                db.flush()
                db.add(
                    WorkflowEventDelivery(
                        id=delivery,
                        subscription_id=sub,
                        subscription_revision=1,
                        project_id=project,
                        workflow_id=row["workflow_id"],
                        source_command_id=command,
                        session_id=session,
                        release_id=row["release_id"],
                        release_digest=row["release_digest"],
                        status="accepted",
                    )
                )
                bindings.append(
                    {
                        **row,
                        "actor_id": str(actor),
                        "channel_id": str(visitor),
                        "source_binding": {
                            "subscription_id": str(sub),
                            "revision": 1,
                            "delivery_id": str(delivery),
                            "source_command_id": str(command),
                            "session_id": str(session),
                        },
                    }
                )
            db.commit()
        output.write_text(json.dumps(bindings, indent=2), encoding="utf-8")
    else:
        bindings = json.loads(output.read_text(encoding="utf-8"))
        with SessionLocal() as db:
            owner = db.get(Project, project)
            assert owner is not None and owner.name == name
            assert owner.api_key == settings.SECRET_KEY
            for row in bindings:
                binding = row["source_binding"]
                sub = db.get(
                    WorkflowEventSubscription, UUID(binding["subscription_id"])
                )
                assert (
                    sub.project_id == project and sub.workflow_id == row["workflow_id"]
                )
                if row["case"] == "disabled":
                    sub.enabled = False
                elif row["case"] == "rebound":
                    sub.revision += 1
                elif row["case"] == "cleared":
                    db.add(
                        ChannelMemoryClearance(
                            project_id=project,
                            user_id=UUID(row["actor_id"]),
                            user_type="staff",
                            channel_id=row["channel_id"],
                            channel_type=251,
                            cleared_message_seq=50,
                            updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
                            + timedelta(seconds=1),
                        )
                    )
                else:
                    actor = db.get(Staff, UUID(row["actor_id"]))
                    assert actor.project_id == project
                    actor.role = "user"
            db.commit()
    sync_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["seed", "mutate"])
    try:
        main(parser.parse_args().action)
    except Exception as error:
        diagnostic = getattr(getattr(error, "orig", None), "diag", None)
        print(
            json.dumps(
                {
                    "error_type": type(error).__name__,
                    "table": getattr(diagnostic, "table_name", None),
                    "column": getattr(diagnostic, "column_name", None),
                    "constraint": getattr(diagnostic, "constraint_name", None),
                }
            )
        )
        raise SystemExit(1)
