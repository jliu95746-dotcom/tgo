"""Verify live tenant authorization with owned, temporary PostgreSQL fixtures."""

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
from app.core.security import create_access_token, get_password_hash  # noqa: E402
from app.models import Project, Staff  # noqa: E402


async def verify(api_base: str) -> None:
    marker = "project-isolation-" + uuid4().hex[:12]
    print(f"Isolated fixture: {marker}")
    project_ids = [uuid4(), uuid4()]
    staff_ids = [uuid4(), uuid4(), uuid4()]
    with SessionLocal() as db:
        db.add_all([
            Project(id=project_id, name=f"{marker}-{index}",
                    api_key=secrets.token_urlsafe(32))
            for index, project_id in enumerate(project_ids)
        ])
        db.flush()
        for index, staff_id in enumerate(staff_ids):
            db.add(Staff(
                id=staff_id, username=f"{marker}-{index}",
                project_id=project_ids[1 if index == 2 else 0],
                role="user" if index == 1 else "admin",
                password_hash=get_password_hash(secrets.token_urlsafe(24)),
                status="offline",
            ))
        db.commit()
    try:
        async with httpx.AsyncClient(base_url=api_base, timeout=15) as client:
            for index in (0, 2):
                own = project_ids[0 if index == 0 else 1]
                other = project_ids[1 if index == 0 else 0]
                token = create_access_token(
                    subject=f"{marker}-{index}", project_id=own, role="admin"
                )
                headers = {"Authorization": f"Bearer {token}"}
                response = await client.get("/v1/projects", headers=headers)
                assert response.status_code == 200
                assert [row["id"] for row in response.json()["data"]] == [str(own)]
                for method, suffix, body, expected in (
                    ("GET", "", None, 404),
                    ("PATCH", "", {"name": "unauthorized"}, 404),
                    ("DELETE", "", None, 404),
                    ("GET", "/ai-config", None, 403),
                    ("PUT", "/ai-config", {}, 403),
                    ("POST", "/ai-config/sync", None, 403),
                ):
                    response = await client.request(
                        method, f"/v1/projects/{other}{suffix}", headers=headers,
                        **({"json": body} if body is not None else {}),
                    )
                    assert response.status_code == expected, (
                        method, suffix, response.status_code
                    )
            token = create_access_token(
                subject=f"{marker}-1", project_id=project_ids[0], role="user"
            )
            headers = {"Authorization": f"Bearer {token}"}
            for method, suffix, body in (
                ("GET", "", None),
                ("GET", f"/{project_ids[0]}", None),
                ("PATCH", f"/{project_ids[0]}", {"name": "unauthorized"}),
                ("DELETE", f"/{project_ids[0]}", None),
                ("PUT", f"/{project_ids[0]}/ai-config", {}),
                ("POST", f"/{project_ids[0]}/ai-config/sync", None),
            ):
                response = await client.request(
                    method, f"/v1/projects{suffix}", headers=headers,
                    **({"json": body} if body is not None else {}),
                )
                assert response.status_code == 403
        with SessionLocal() as db:
            for index, project_id in enumerate(project_ids):
                project = db.get(Project, project_id)
                assert project and project.deleted_at is None
                assert project.name == f"{marker}-{index}"
        print("PASS: live PostgreSQL/JWT/HTTP tenant isolation; no foreign access")
    finally:
        with SessionLocal() as db:
            projects = db.scalars(
                select(Project).where(Project.id.in_(project_ids))
            ).all()
            staff = db.scalars(select(Staff).where(Staff.id.in_(staff_ids))).all()
            assert len(projects) == 2 and len(staff) == 3
            assert all(row.name.startswith(marker) for row in projects)
            assert all(row.username.startswith(marker) for row in staff)
            assert all(row.project_id in project_ids for row in staff)
            db.execute(delete(Staff).where(Staff.id.in_(staff_ids)))
            db.execute(delete(Project).where(Project.id.in_(project_ids)))
            db.commit()
        print("CLEANUP: removed only 2 owned test projects and 3 owned test accounts")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", required=True)
    args = parser.parse_args()
    if urlparse(args.api_base).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("This fixture is restricted to a local native API")
    asyncio.run(verify(args.api_base))
