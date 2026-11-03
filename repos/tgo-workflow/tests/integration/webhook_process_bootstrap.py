"""Disposable listeners and worker using production business handlers."""

import argparse
import os
from pathlib import Path
import re
import sys
from uuid import UUID, uuid4

sys.path.insert(0, os.getcwd())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=("api", "workflow", "worker", "beat"))
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    prefix = os.environ["WEBHOOK_TEST_PREFIX"]
    if not re.fullmatch(r"webhook-test-[a-f0-9]{32}:", prefix):
        raise RuntimeError("A newly owned test namespace is required")
    if args.role == "api":
        start_api(args.port, prefix)
    else:
        start_workflow(args.role, args.port, prefix)


def start_api(port, prefix):
    import uvicorn
    from fastapi import FastAPI, Header, HTTPException
    from sqlalchemy import create_engine, update
    from sqlalchemy.orm import Session
    from app.core.config import settings
    from app.core.database import Base, get_db
    from app.models.project import Project
    from app.models.staff import Staff
    from app.api.internal.endpoints import workflow_webhooks as authority
    from app.api.v1.endpoints import workflow_webhook_ingress as ingress
    from app.api.v1.endpoints import workflow_webhooks as management
    from app.api.v1.endpoints import ai_workflows, staff
    from app.core.security import get_password_hash
    from app.services.public_webhook_rate import admit_public_webhook

    location = Path(os.environ["WEBHOOK_TEST_API_DB"]).resolve()
    workspace = Path(__file__).resolve().parents[4]
    if not location.is_relative_to(workspace / ".tmp") or location.exists():
        raise RuntimeError(
            "Only a new workspace test identity database is allowed"
        )
    engine = create_engine(
        "sqlite:///" + location.as_posix(),
        connect_args={"check_same_thread": False},
        hide_parameters=True,
    )
    Base.metadata.create_all(
        engine, tables=[Project.__table__, Staff.__table__]
    )
    project, actor = UUID(os.environ["WEBHOOK_TEST_PROJECT"]), UUID(
        os.environ["WEBHOOK_TEST_ACTOR"]
    )
    with Session(engine) as db:
        db.add(
            Project(
                id=project, name="Webhook isolated test", api_key=uuid4().hex
            )
        )
        db.flush()
        db.add(
            Staff(
                id=actor,
                project_id=project,
                username=prefix[:-1],
                password_hash=get_password_hash(
                    os.environ["WEBHOOK_TEST_PASSWORD"]
                ),
                role="admin",
                account_enabled=True,
                token_version=1,
            )
        )
        db.commit()
    app = FastAPI()
    app.include_router(authority.router, prefix="/internal/workflows/webhooks")
    app.include_router(ingress.router, prefix="/v1/workflow-webhooks")
    app.include_router(management.router, prefix="/v1/ai/workflows")
    app.include_router(ai_workflows.router, prefix="/v1/ai/workflows")
    app.include_router(staff.router, prefix="/v1/staff")

    def database():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = database

    async def scoped_rate(host):
        await admit_public_webhook(prefix + host)

    ingress.admit_public_webhook = scoped_rate

    @app.get("/health")
    def health():
        return {"scope": prefix}

    @app.post("/_test/revoke")
    def revoke(x_internal_api_key: str | None = Header(None)):
        if x_internal_api_key != settings.SECRET_KEY:
            raise HTTPException(403)
        with Session(engine) as db:
            db.execute(
                update(Staff)
                .where(Staff.id == actor)
                .values(account_enabled=False)
            )
            db.commit()
        return {"revoked": True}

    # No main-app lifecycle tasks or real identity configuration are started.
    uvicorn.run(
        app, host="127.0.0.1", port=port, access_log=False, log_level="warning"
    )


def start_workflow(role, port, prefix):
    import uvicorn
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.config import settings
    import app.database as database
    import celery_app.task_database as task_database
    from celery_app.celery import celery_app

    url = make_url(settings.DATABASE_URL)
    schema = os.environ["WEBHOOK_TEST_SCHEMA"]
    if (
        url.host not in {"127.0.0.1", "localhost", "::1"}
        or not (url.database or "").startswith("tgo_scenarios_test_webhook_")
        or not re.fullmatch(r"schedule_[a-f0-9]{32}", schema)
    ):
        raise RuntimeError(
            "Only an owned loopback test database/schema is allowed"
        )
    connection = {"server_settings": {"search_path": schema}}
    database.engine = create_async_engine(
        settings.DATABASE_URL, connect_args=connection, hide_parameters=True
    )
    database.AsyncSessionLocal.configure(bind=database.engine)
    original = task_database.create_async_engine

    def task_engine(target, **kwargs):
        return original(target, connect_args=connection, **kwargs)

    task_database.create_async_engine = task_engine
    celery_app.conf.broker_transport_options = {"global_keyprefix": prefix}
    celery_app.conf.result_backend_transport_options = {
        "global_keyprefix": prefix
    }
    if role == "workflow":
        from celery_app.webhook_tasks import dispatch_webhook_delivery_task

        def lost_ack(**kwargs):
            raise OSError("synthetic-source-kick-acknowledgement-loss")

        dispatch_webhook_delivery_task.apply_async = lost_ack
        uvicorn.run(
            "app.main:app",
            host="127.0.0.1",
            port=port,
            access_log=False,
            log_level="warning",
        )
    elif role == "beat":
        # Keep the production schedule and interval. The persistent schedule
        # belongs to this fixture and is removed with its temporary directory.
        schedule = Path(os.environ["WEBHOOK_TEST_API_DB"]).with_name(
            "owned-webhook-beat"
        )
        celery_app.start(
            [
                "beat", "--schedule", str(schedule),
                "--max-interval", "1", "--loglevel=WARNING",
            ]
        )
    else:
        celery_app.worker_main(
            [
                "worker",
                "--pool=solo",
                "--concurrency=1",
                "--without-mingle",
                "--without-gossip",
                "--without-heartbeat",
                "-Q",
                settings.WORKFLOW_QUEUE_NAME,
                "-n",
                settings.WORKFLOW_QUEUE_NAME,
                "--loglevel=WARNING",
            ]
        )


if __name__ == "__main__":
    main()
