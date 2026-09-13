"""Exercise local public signup/login with two owned, isolated project fixtures."""

import argparse
import asyncio
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, select

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "repos" / "tgo-api"))

from app.core.database import SessionLocal  # noqa: E402
from app.models import AIProvider, Platform, Project, Staff  # noqa: E402


async def verify(api_base: str) -> None:
    marker = "signup-e2e-" + uuid4().hex[:12]
    print(f"Isolated fixture: {marker}")
    usernames = [f"{marker}-{index}@example.com" for index in range(2)]
    password = secrets.token_urlsafe(24)
    project_ids: list[UUID] = []
    tokens: list[str] = []
    try:
        async with httpx.AsyncClient(
            base_url=api_base, timeout=30, follow_redirects=True
        ) as client:
            for index, username in enumerate(usernames):
                response = await client.post(
                    "/v1/staff/register",
                    json={
                        "username": username,
                        "password": password,
                        "project_name": f"{marker}-{index}",
                    },
                )
                assert response.status_code == 201, ("signup", response.status_code)
                account = response.json()
                assert account["role"] == "admin"
                assert "password_hash" not in account and "api_key" not in account
                project_ids.append(UUID(account["project_id"]))
                login = await client.post(
                    "/v1/staff/login",
                    data={"username": username.upper(), "password": password},
                )
                assert login.status_code == 200, ("login", login.status_code)
                assert login.json()["staff"]["project_id"] == str(project_ids[-1])
                tokens.append(login.json()["access_token"])
            assert project_ids[0] != project_ids[1]
            duplicate = await client.post(
                "/v1/staff/register",
                json={"username": usernames[0].upper(), "password": password},
            )
            assert duplicate.status_code == 409
            injected = await client.post(
                "/v1/staff/register",
                json={
                    "username": usernames[0],
                    "password": password,
                    "project_id": str(project_ids[1]),
                    "role": "admin",
                },
            )
            assert injected.status_code == 422
            for index, token in enumerate(tokens):
                headers = {"Authorization": f"Bearer {token}"}
                own, other = project_ids[index], project_ids[1 - index]
                projects = await client.get("/v1/projects", headers=headers)
                assert projects.status_code == 200
                assert [row["id"] for row in projects.json()["data"]] == [str(own)]
                denied = await client.get(f"/v1/projects/{other}", headers=headers)
                assert denied.status_code == 404
                me = await client.get("/v1/staff/me", headers=headers)
                assert me.status_code == 200 and me.json()["project_id"] == str(own)
                platforms = await client.get("/v1/platforms", headers=headers)
                assert platforms.status_code == 200
                rows = platforms.json()["data"]
                assert len(rows) == 1 and rows[0]["type"] == "website"
                providers = await client.get("/v1/ai/providers", headers=headers)
                assert providers.status_code == 200 and providers.json()["data"] == []
            with SessionLocal() as db:
                assert (
                    db.query(Staff).filter(Staff.username.in_(usernames)).count() == 2
                )
                sites = db.scalars(
                    select(Platform).where(Platform.project_id.in_(project_ids))
                ).all()
                assert len(sites) == 2 and all(site.ai_mode == "off" for site in sites)
                assert not db.scalars(
                    select(AIProvider).where(AIProvider.project_id.in_(project_ids))
                ).first()
        print(
            "PASS: live signup, login, duplicate/injection rejection, project isolation"
        )
        print("PASS: each project has a manual website; no model credentials copied")
    finally:
        # Resolve by the exact generated account names even if an HTTP receipt was lost.
        with SessionLocal() as db:
            staff = db.scalars(select(Staff).where(Staff.username.in_(usernames))).all()
            owned_ids = [row.project_id for row in staff]
            projects = db.scalars(
                select(Project).where(Project.id.in_(owned_ids))
            ).all()
            assert len(projects) == len(staff) <= 2
            assert all(row.name in {f"{marker}-0", f"{marker}-1"} for row in projects)
            platforms = db.scalars(
                select(Platform).where(Platform.project_id.in_(owned_ids))
            ).all()
            assert len(platforms) == len(staff)
            assert all(
                row.type == "website" and row.ai_mode == "off" for row in platforms
            )
            db.execute(
                delete(Platform).where(Platform.id.in_([row.id for row in platforms]))
            )
            db.execute(delete(Staff).where(Staff.id.in_([row.id for row in staff])))
            db.execute(delete(Project).where(Project.id.in_(owned_ids)))
            db.commit()
        print(
            f"CLEANUP: removed {len(staff)} owned accounts, projects and websites"
        )
        print(
            "Only empty synthetic IM identities/channels may remain; no messages sent"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", required=True)
    args = parser.parse_args()
    if urlparse(args.api_base).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("This fixture is restricted to a local native API")
    asyncio.run(verify(args.api_base))
