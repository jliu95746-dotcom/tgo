"""Read-only existing-company inventory: no automatic billing enrollment."""

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import Project, Staff
from app.schemas.base import PaginationMetadata
from app.schemas.operations import (
    CompanyMigrationPreview, MigrationPreviewResponse,
)


def preview_company_migration(
    db: Session, limit: int, offset: int
) -> MigrationPreviewResponse:
    counts = (
        select(
            Staff.project_id,
            func.count(Staff.id).label("human_accounts"),
            func.sum(case((Staff.role == "admin", 1), else_=0)).label(
                "admins",
            ),
        )
        .where(
            Staff.deleted_at.is_(None),
            Staff.role.in_(["admin", "user"]),
        )
        .group_by(Staff.project_id)
        .subquery()
    )
    rows = db.execute(
        select(
            Project,
            func.coalesce(counts.c.human_accounts, 0),
            func.coalesce(counts.c.admins, 0),
        )
        .outerjoin(counts, counts.c.project_id == Project.id)
        .where(Project.deleted_at.is_(None))
        .order_by(Project.created_at, Project.id)
        .limit(limit)
        .offset(offset)
    ).all()
    total = (
        db.scalar(
            select(func.count(Project.id)).where(Project.deleted_at.is_(None))
        )
        or 0
    )
    return MigrationPreviewResponse(
        data=[
            CompanyMigrationPreview(
                project_id=project.id,
                name=project.name,
                created_at=project.created_at,
                human_accounts=humans,
                administrator_accounts=admins,
                requires_admin_recovery=admins == 0,
            )
            for project, humans, admins in rows
        ],
        pagination=PaginationMetadata(
            total=total,
            limit=limit,
            offset=offset,
            has_next=offset + limit < total,
            has_prev=offset > 0,
        ),
    )
