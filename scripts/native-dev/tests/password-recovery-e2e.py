"""Verify operator recovery against local DB/login using only owned test accounts."""

import argparse
import asyncio
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from sqlalchemy import delete, select

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "repos" / "tgo-api"))

from app.core.database import SessionLocal  # noqa: E402
from app.core.security import get_password_hash  # noqa: E402
from app.models import Project, Staff  # noqa: E402
from scripts.reset_staff_password import run_recovery  # noqa: E402


async def verify(api_base: str) -> None:
    marker = "recovery-e2e-" + uuid4().hex[:12]
    print(f"Isolated fixture: {marker}")
    project_ids = [uuid4(), uuid4()]
    staff_ids = [uuid4(), uuid4()]
    usernames = [f"{marker}-{index}@example.com" for index in range(2)]
    old_password, new_password = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    with SessionLocal() as db:
        db.add_all(
            [
                Project(
                    id=project_id,
                    name=f"{marker}-{index}",
                    api_key=secrets.token_urlsafe(32),
                )
                for index, project_id in enumerate(project_ids)
            ]
        )
        db.flush()
        db.add_all(
            [
                Staff(
                    id=staff_id,
                    project_id=project_ids[index],
                    username=usernames[index],
                    password_hash=get_password_hash(old_password),
                    role="admin",
                    status="offline",
                )
                for index, staff_id in enumerate(staff_ids)
            ]
        )
        db.commit()
    try:
        with SessionLocal() as db:
            prompts = iter([usernames[0], str(project_ids[0])])
            output: list[str] = []
            assert run_recovery(
                db, lambda _: next(prompts), lambda _: new_password, output.append
            )
            assert new_password not in "".join(output)
        async with httpx.AsyncClient(base_url=api_base, timeout=30) as client:
            for username, password, expected in (
                (usernames[0], old_password, 401),
                (usernames[0], new_password, 200),
                (usernames[1], old_password, 200),
                (usernames[1], new_password, 401),
            ):
                response = await client.post(
                    "/v1/staff/login", data={"username": username, "password": password}
                )
                assert response.status_code == expected, (
                    "login",
                    response.status_code,
                    expected,
                )
        print(
            "PASS: recovered account accepts new password only; other project unchanged"
        )
        print("PASS: actual operator flow never prints the password")
    finally:
        with SessionLocal() as db:
            projects = db.scalars(
                select(Project).where(Project.id.in_(project_ids))
            ).all()
            staff = db.scalars(select(Staff).where(Staff.id.in_(staff_ids))).all()
            assert len(projects) == len(staff) == 2
            assert all(row.name in {f"{marker}-0", f"{marker}-1"} for row in projects)
            assert all(
                row.username in usernames and row.project_id in project_ids
                for row in staff
            )
            db.execute(delete(Staff).where(Staff.id.in_(staff_ids)))
            db.execute(delete(Project).where(Project.id.in_(project_ids)))
            db.commit()
        print(
            "CLEANUP: removed only 2 owned accounts/projects; no real password changed"
        )
        print("Empty synthetic IM identities/channels may remain; no messages sent")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", required=True)
    args = parser.parse_args()
    if urlparse(args.api_base).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("This fixture is restricted to a local native API")
    asyncio.run(verify(args.api_base))
