"""Real operator HTTP lifecycle and migration preview in rolled-back SQL."""

import asyncio
import importlib.util
import logging
import socket
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from secrets import token_urlsafe
from unittest.mock import patch
from uuid import uuid4

import httpx
import uvicorn
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "repos/tgo-api"))

from app.api.v1.endpoints import operations  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import get_db, sync_engine  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Project, Staff  # noqa: E402
from app.models.platform_operator import PlatformOperator  # noqa: E402
from app.schemas.operations import OperatorCreateRequest  # noqa: E402
from app.services.operations_auth import create_operator  # noqa: E402


def seed(connection, password):
    for model in (Project, Staff):
        model.__table__.create(connection)
    spec = importlib.util.spec_from_file_location(
        "private_operators_migration",
        ROOT / "repos/tgo-api/alembic/versions/0037_platform_operators.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
    with Session(connection, join_transaction_mode="create_savepoint") as db:
        projects = [
            Project(name=name, api_key=token_urlsafe(24))
            for name in ("normal", "without-admin", "empty", "deleted")
        ]
        projects[3].deleted_at = datetime.now(timezone.utc)
        db.add_all(projects)
        db.flush()
        for index, role, deleted in (
            (0, "admin", False),
            (0, "user", False),
            (0, "agent", False),
            (0, "admin", True),
            (1, "user", False),
            (3, "admin", False),
        ):
            db.add(
                Staff(
                    project_id=projects[index].id,
                    username="fixture-" + uuid4().hex,
                    password_hash="cannot-login-fixture",
                    role=role,
                    deleted_at=datetime.now(timezone.utc) if deleted else None,
                )
            )
        db.commit()
        operator = create_operator(
            db,
            OperatorCreateRequest(
                email="isolated-operator@example.com",
                name="临时运营验收",
                password=SecretStr(password),
            ),
        )
        admin = db.scalar(
            select(Staff).where(
                Staff.project_id == projects[0].id,
                Staff.role == "admin",
                Staff.deleted_at.is_(None),
            )
        )
        tenant_token = create_access_token(
            admin.username,
            projects[0].id,
            role="admin",
        )
        return operator.id, tenant_token


def snapshot_companies(connection):
    with Session(connection, join_transaction_mode="create_savepoint") as db:
        return (
            tuple(
                db.execute(
                    select(
                        Project.id,
                        Project.name,
                        Project.api_key,
                        Project.deleted_at,
                    ).order_by(Project.id)
                ).all()
            ),
            tuple(
                db.execute(
                    select(
                        Staff.id,
                        Staff.project_id,
                        Staff.role,
                        Staff.deleted_at,
                    ).order_by(Staff.id)
                ).all()
            ),
        )


async def check_http(client, connection, operator_id, password, tenant_token):
    async def login(expected=200):
        response = await client.post(
            "/v1/ops/login",
            json={
                "email": "isolated-operator@example.com",
                "password": password,
            },
        )
        assert response.status_code == expected, response.status_code
        if expected == 200:
            assert response.headers["cache-control"] == "no-store"
            return {
                "Authorization": "Bearer " + response.json()["access_token"]
            }

    async def denied(headers):
        for path in ("/v1/ops/me", "/v1/ops/migration-preview"):
            response = await client.get(path, headers=headers)
            assert response.status_code == 401, (path, response.status_code)
        response = await client.post("/v1/ops/logout", headers=headers)
        assert response.status_code == 401

    def mutate(**values):
        with Session(
            connection, join_transaction_mode="create_savepoint"
        ) as db:
            operator = db.get(PlatformOperator, operator_id)
            for key, value in values.items():
                setattr(operator, key, value)
            db.commit()

    headers = await login()
    before = snapshot_companies(connection)
    rows = []
    for offset in (0, 2):
        response = await client.get(
            "/v1/ops/migration-preview",
            headers=headers,
            params={"offset": offset, "limit": 2},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["will_change_data"] is False
        assert result["pagination"]["total"] == 3
        assert result["pagination"]["has_next"] == (offset == 0)
        rows.extend(result["data"])
        assert all(
            word not in response.text
            for word in (
                "api_key",
                "password",
                "token_version",
            )
        )
    assert len({r["project_id"] for r in rows}) == 3
    assert {
        r["name"]: (
            r["human_accounts"],
            r["administrator_accounts"],
            r["requires_admin_recovery"],
            r["action"],
        )
        for r in rows
    } == {
        "normal": (2, 1, False, "review_required"),
        "without-admin": (1, 0, True, "review_required"),
        "empty": (0, 0, True, "review_required"),
    }
    assert snapshot_companies(connection) == before
    await denied({"Authorization": "Bearer " + tenant_token})
    assert (
        await client.get(
            "/v1/projects",
            headers=headers,
        )
    ).status_code == 401
    print(
        "PASS: live preview counts, recovery flags, pagination, "
        "non-mutation and identity separation",
        flush=True,
    )

    mutate(is_active=False)
    await denied(headers)
    await login(401)
    # Simulate local maintenance explicitly revoking all previous sessions.
    mutate(is_active=True, token_version=2)
    await denied(headers)
    headers = await login()
    assert (await client.get("/v1/ops/me", headers=headers)).status_code == 200
    mutate(deleted_at=datetime.now(timezone.utc))
    await denied(headers)
    await login(401)
    mutate(deleted_at=None, token_version=3)
    await denied(headers)
    headers = await login()
    assert (
        await client.post(
            "/v1/ops/logout",
            headers=headers,
        )
    ).status_code == 204
    await denied(headers)
    headers = await login()
    mutate(token_version=5)
    await denied(headers)
    assert snapshot_companies(connection) == before
    print(
        "PASS: disabled/deleted accounts, logout and version revocation "
        "reject old credentials over HTTP",
        flush=True,
    )


async def main():
    logging.disable(logging.CRITICAL)
    marker = "ops_lifecycle_" + uuid4().hex
    keys = set()
    original_limit = operations.limit_operator_login

    async def private_limit(host, email):
        scoped_host, scoped_email = marker + host, marker + email
        for dimension, value in (
            ("host", scoped_host),
            ("email", scoped_email),
        ):
            digest = sha256(value.encode()).hexdigest()
            keys.add(f"ops:login:{dimension}:{digest}")
        await original_limit(scoped_host, scoped_email)

    with sync_engine.connect() as connection:
        transaction = connection.begin()
        server = server_task = None
        listener = None
        try:
            connection.execute(text(f'CREATE SCHEMA "{marker}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{marker}"'))
            password = token_urlsafe(24)
            operator_id, tenant_token = seed(connection, password)

            def private_db():
                with Session(
                    connection,
                    join_transaction_mode="create_savepoint",
                ) as db:
                    yield db

            with (
                patch.object(settings, "SAAS_ENABLED", False),
                patch.object(settings, "OPS_ENABLED", True),
                patch.object(settings, "SAAS_NEW_PURCHASES_ENABLED", False),
                patch.object(
                    settings, "OPS_SECRET_KEY", SecretStr(token_urlsafe(48))
                ),
                patch.object(
                    operations, "limit_operator_login", private_limit
                ),
            ):
                app = create_app()
                app.dependency_overrides[get_db] = private_db
                listener = socket.socket()
                listener.bind(("127.0.0.1", 0))
                listener.listen(16)
                listener.setblocking(False)
                port = listener.getsockname()[1]
                server = uvicorn.Server(
                    uvicorn.Config(
                        app,
                        lifespan="off",
                        log_level="critical",
                        access_log=False,
                    )
                )
                server_task = asyncio.create_task(
                    server.serve(sockets=[listener])
                )
                for _ in range(100):
                    if server.started:
                        break
                    if server_task.done():
                        await server_task
                        raise AssertionError(
                            "HTTP fixture stopped during startup"
                        )
                    await asyncio.sleep(0.05)
                assert server.started
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}",
                    timeout=15,
                ) as client:
                    await check_http(
                        client,
                        connection,
                        operator_id,
                        password,
                        tenant_token,
                    )
        finally:
            if server is not None:
                server.should_exit = True
            if server_task is not None:
                await asyncio.wait_for(server_task, 15)
            if listener is not None:
                listener.close()
            transaction.rollback()
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.schemata "
                        "WHERE schema_name=:schema"
                    ),
                    {"schema": marker},
                )
                == 0
            )
            if keys:
                async with Redis.from_url(settings.REDIS_URL) as redis:
                    await redis.delete(*keys)
                    assert await redis.exists(*keys) == 0
            print(
                "PASS: private HTTP server stopped, SQL schema rolled back "
                "and owned login-limit keys removed",
                flush=True,
            )


if __name__ == "__main__":
    asyncio.run(main())
