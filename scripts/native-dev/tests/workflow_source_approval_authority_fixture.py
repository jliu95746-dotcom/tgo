"""Owned API source facts for real approval execution, without customer data."""

import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import logging
import os
from pathlib import Path
from uuid import UUID, uuid4

logging.disable(logging.CRITICAL)


def main(action):
    spec = importlib.util.spec_from_file_location(
        "source_approval_staff_fixture",
        Path(__file__).with_name("workflow_timer_authority_fixture.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # This validates exact disposable database/project/actor ownership first.
    module.main("seed" if action == "seed" else "grant")
    if action == "seed":
        return
    from app.models import Platform, Visitor, VisitorSession, SessionCommandDelivery
    from app.models.channel_memory_clearance import ChannelMemoryClearance
    from app.models.workflow_event_subscription import (
        WorkflowEventSubscription,
        WorkflowEventDelivery,
    )

    project = UUID(os.environ["TGO_TIMER_TEST_PROJECT"])
    actor = UUID(os.environ["TGO_TIMER_TEST_ACTOR"])
    database = os.environ["TGO_TIMER_TEST_DATABASE"]
    root = Path(__file__).resolve().parents[3]
    directory = (
        root / ".tmp/native-dev" / ("source-approval-" + database[-32:])
    ).resolve()
    output = Path(os.environ["TGO_SOURCE_APPROVAL_BINDINGS"]).resolve()
    assert output == directory / "source-bindings.json"
    if action == "bind":
        cases = json.loads(os.environ["TGO_SOURCE_APPROVAL_CASES"])
        assert len(cases) == 6 and {row["case"] for row in cases} == {
            "happy",
            "recovery",
            "disabled",
            "rebound",
            "cleared",
            "staff_revoked",
        }
        tables = {
            model.__table__
            for model in (
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
            for foreign in pending.pop().foreign_keys:
                table = foreign.column.table
                if table not in tables:
                    tables.add(table)
                    pending.append(table)
        module.Base.metadata.create_all(module.sync_engine, tables=list(tables))
        now = datetime.now(timezone.utc)
        version = now.replace(tzinfo=None)
        bindings = []
        with module.SessionLocal() as db:
            for row in cases:
                platform, visitor, session, command, sub, delivery = [
                    uuid4() for _ in range(6)
                ]
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
                        from_uid="synthetic-staff",
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
        cases = {row["case"]: row for row in bindings}
        with module.SessionLocal() as db:
            if action == "revoke":
                db.get(module.Staff, actor).role = "user"
            else:
                row = cases[
                    {"disable": "disabled", "rebind": "rebound", "clear": "cleared"}[
                        action
                    ]
                ]
                binding = row["source_binding"]
                sub = db.get(
                    WorkflowEventSubscription, UUID(binding["subscription_id"])
                )
                assert (
                    sub.project_id == project and sub.workflow_id == row["workflow_id"]
                )
                if action == "disable":
                    sub.enabled = False
                elif action == "rebind":
                    sub.revision += 1
                else:
                    db.add(
                        ChannelMemoryClearance(
                            project_id=project,
                            user_id=actor,
                            user_type="staff",
                            channel_id=row["channel_id"],
                            channel_type=251,
                            cleared_message_seq=50,
                            updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
                        )
                    )
            db.commit()
    module.sync_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=["seed", "bind", "disable", "rebind", "clear", "revoke"]
    )
    main(parser.parse_args().action)
