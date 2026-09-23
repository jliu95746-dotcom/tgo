"""Real HTTP password login and API-to-RAG calls; API-owned private DB."""
import asyncio
import json
import logging
import secrets
import socket
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import uvicorn
from sqlalchemy import text
from sqlalchemy.orm import Session

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root / "repos/tgo-api"))
from app.api.v1.endpoints import staff  # noqa: E402
from app.core.database import sync_engine, get_db  # noqa: E402
from app.core.security import get_password_hash  # noqa: E402
from app.models import Project, Staff  # noqa: E402
from app.services.rag_client import rag_client  # noqa: E402
from app.main import create_app  # noqa: E402


async def check_user(client, db, account, password, own, other):
    logged_in = await client.post(
        "/v1/staff/login",
        data={
            "username": account.username,
            "password": password,
        },
    )
    assert logged_in.status_code == 200, f"Login HTTP {logged_in.status_code}"
    headers = {"Authorization": "Bearer " + logged_in.json()["access_token"]}
    # Frontend-supplied company identifiers must not override authentication.
    params = {"project_id": other["project"]}
    paths = [
        (
            f'/v1/rag/collections/{own["faq"]}',
            f'/v1/rag/collections/{other["faq"]}',
        ),
        (f'/v1/rag/{own["faq"]}/qa-pairs', f'/v1/rag/{other["faq"]}/qa-pairs'),
        (
            f'/v1/rag/qa-pairs/{own["pair"]}',
            f'/v1/rag/qa-pairs/{other["pair"]}',
        ),
        (
            f'/v1/rag/websites/pages/{own["page"]}',
            f'/v1/rag/websites/pages/{other["page"]}',
        ),
    ]
    for allowed_path, denied_path in paths:
        allowed = await client.get(
            allowed_path, params=params, headers=headers
        )
        assert (
            allowed.status_code == 200
        ), f"Own read {allowed_path}: HTTP {allowed.status_code}"
        forbidden = await client.get(
            denied_path, params=params, headers=headers
        )
        assert (
            forbidden.status_code == 404
        ), f"Foreign read {denied_path}: HTTP {forbidden.status_code}"
    for key, value in (("faq", other["faq"]), ("site", other["site"])):
        path = (
            "/v1/rag/websites/pages"
            if key == "site"
            else "/v1/rag/qa-categories"
        )
        response = await client.get(
            path, params={**params, "collection_id": value}, headers=headers
        )
        if key == "site":
            assert response.status_code == 404
        else:
            assert (
                response.status_code == 200
                and response.json()["categories"] == []
            )
    body = {
        "question": "forbidden question",
        "answer": "forbidden answer",
        "project_id": other["project"],
    }
    for method, path, payload in [
        ("POST", f'/v1/rag/{other["faq"]}/qa-pairs', body),
        ("PUT", f'/v1/rag/qa-pairs/{other["pair"]}', {"answer": "forbidden"}),
        ("DELETE", f'/v1/rag/qa-pairs/{other["pair"]}', None),
        ("DELETE", f'/v1/rag/websites/pages/{other["page"]}', None),
        ("POST", f'/v1/rag/websites/pages/{other["page"]}/recrawl', None),
        (
            "POST",
            f'/v1/rag/websites/pages/{other["page"]}/crawl-deeper',
            {"max_depth": 2},
        ),
    ]:
        denied = await client.request(
            method, path, params=params, headers=headers, json=payload
        )
        assert (
            denied.status_code == 404
        ), f"Foreign mutation {path}: HTTP {denied.status_code}"
    anonymous = await client.get(paths[0][0])
    assert (
        anonymous.status_code == 403
    ), f"Anonymous HTTP {anonymous.status_code}"
    account.deleted_at = datetime.utcnow()
    db.commit()
    revoked = await client.get(paths[0][0], headers=headers)
    assert revoked.status_code == 401
    print(
        f"PASS: {account.role} password login, tenant-bound reads/writes, "
        "anonymous and revoked-token denial",
        flush=True,
    )


async def main():
    logging.disable(logging.CRITICAL)
    manifest = json.loads(sys.argv[1])
    schema = "api_knowledge_http_" + uuid4().hex
    server = server_task = None
    with sync_engine.connect() as connection:
        outer = connection.begin()
        try:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            for model in (Project, Staff):
                model.__table__.create(connection)
            with Session(
                connection,
                join_transaction_mode="create_savepoint",
                expire_on_commit=False,
            ) as db:
                password = secrets.token_urlsafe(24)
                password_hash = get_password_hash(password)
                accounts = []
                for tenant in manifest["tenants"]:
                    project = Project(
                        id=UUID(tenant["project"]),
                        name="synthetic",
                        api_key=uuid4().hex,
                    )
                    db.add(project)
                    db.flush()
                    members = [
                        Staff(
                            project_id=project.id,
                            username="synthetic-" + uuid4().hex,
                            password_hash=password_hash,
                            role=role,
                        )
                        for role in ("admin", "user")
                    ]
                    db.add_all(members)
                    accounts.append(members)
                db.commit()
                app = create_app()
                app.dependency_overrides[get_db] = lambda: db
                rag_client.base_url = manifest["base_url"]
                # IM synchronization is outside this knowledge check.
                staff.wukongim_client.register_or_login_user = AsyncMock()
                staff.ensure_project_staff_channel = AsyncMock()
                listener = socket.socket()
                listener.bind(("127.0.0.1", 0))
                listener.listen(64)
                base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
                server = uvicorn.Server(
                    uvicorn.Config(
                        app,
                        log_level="critical",
                        access_log=False,
                        lifespan="off",
                    )
                )
                server_task = asyncio.create_task(
                    server.serve(sockets=[listener])
                )
                for _ in range(100):
                    if server.started:
                        break
                    await asyncio.sleep(0.05)
                assert server.started
                async with httpx.AsyncClient(
                    base_url=base_url, timeout=30
                ) as client:
                    for index, members in enumerate(accounts):
                        for account in members:
                            await check_user(
                                client,
                                db,
                                account,
                                password,
                                manifest["tenants"][index],
                                manifest["tenants"][1 - index],
                            )
        finally:
            if server:
                server.should_exit = True
            if server_task:
                await asyncio.wait_for(server_task, 10)
            outer.rollback()
    with sync_engine.connect() as check:
        assert (
            check.scalar(
                text(
                    "SELECT count(*) FROM pg_namespace WHERE nspname=:schema"
                ),
                {"schema": schema},
            )
            == 0
        )
    print(
        "PASS: temporary API server stopped; API fixture schema rolled back",
        flush=True,
    )


asyncio.run(main())
