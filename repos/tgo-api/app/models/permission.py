"""Permission models for RBAC."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional, List
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.project import Project


class Permission(Base):
    (
        'Permission definition model.\n'
        '    \n'
        '    Defines available permissions in the system using '
        'resource:action format.\n'
        '    Examples: staff:create, staff:read, ai_agents:update\n'
        '    '
    )

    __tablename__ = "api_permissions"

    # Primary key
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    # Permission definition
    resource: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Resource name: staff, ai_agents, rag_collections, etc.",
    )
    action: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Action: create, read, update, delete, list",
    )
    description: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Human-readable description of the permission",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, default=func.now(), comment="Creation timestamp"
    )

    # Relationships
    role_permissions: Mapped[List["RolePermission"]] = relationship(
        "RolePermission", back_populates="permission", lazy="select"
    )
    project_role_permissions: Mapped[
        List["ProjectRolePermission"]
    ] = relationship(
        "ProjectRolePermission", back_populates="permission", lazy="select"
    )

    # Constraints
    __table_args__ = (
        UniqueConstraint(
            "resource", "action", name="uq_permission_resource_action"
        ),
    )

    def __repr__(self) -> str:
        """String representation of the permission."""
        return (
            f'<Permission(id='
            f'{self.id}'
            f", resource='"
            f'{self.resource}'
            f"', action='"
            f'{self.action}'
            f"')>"
        )

    @property
    def code(self) -> str:
        """Get permission code in resource:action format."""
        return f"{self.resource}:{self.action}"


class RolePermission(Base):
    (
        'Global Role-Permission association model.\n'
        '    \n'
        '    Defines default permissions for roles across ALL projects.\n'
        '    When a new permission is added, only this table needs to be '
        'updated.\n'
        '    All projects automatically inherit these permissions.\n'
        '    \n'
        '    Final project permissions = RolePermission (global) + '
        'ProjectRolePermission (project-specific)\n'
        '    '
    )

    __tablename__ = "api_role_permissions"

    # Primary key
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    # Role and permission (no project association - global)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="Role name: user, admin, agent"
    )
    permission_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_permissions.id", ondelete="CASCADE"),
        nullable=False,
        comment="Associated permission ID",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, default=func.now(), comment="Creation timestamp"
    )

    # Relationships
    permission: Mapped["Permission"] = relationship(
        "Permission", back_populates="role_permissions", lazy="select"
    )

    # Constraints - unique per role + permission (global, no project)
    __table_args__ = (
        UniqueConstraint("role", "permission_id", name="uq_role_permission"),
    )

    def __repr__(self) -> str:
        """String representation of the global role permission."""
        return (
            f'<RolePermission(id='
            f'{self.id}'
            f", role='"
            f'{self.role}'
            f"', permission_id="
            f'{self.permission_id}'
            f')>'
        )


class ProjectRolePermission(Base):
    (
        'Project-specific Role-Permission association model.\n'
        '    \n'
        '    Defines additional permissions for roles within a specific '
        'project.\n'
        '    These permissions are MERGED with global RolePermission.\n'
        '    Projects can only ADD permissions, not disable global ones.\n'
        '    \n'
        '    Final project permissions = RolePermission (global) + '
        'ProjectRolePermission (project-specific)\n'
        '    '
    )

    __tablename__ = "api_project_role_permissions"

    # Primary key
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    # Role and permission with project association
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="Role name: user, admin, agent"
    )
    permission_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_permissions.id", ondelete="CASCADE"),
        nullable=False,
        comment="Associated permission ID",
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="Associated project ID for project-specific permissions",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, default=func.now(), comment="Creation timestamp"
    )

    # Relationships
    permission: Mapped["Permission"] = relationship(
        "Permission", back_populates="project_role_permissions", lazy="select"
    )
    project: Mapped["Project"] = relationship("Project", lazy="select")

    # Constraints - unique per role + permission + project
    __table_args__ = (
        UniqueConstraint(
            "role",
            "permission_id",
            "project_id",
            name="uq_project_role_permission",
        ),
    )

    def __repr__(self) -> str:
        """String representation of the project role permission."""
        return (
            f'<ProjectRolePermission(id='
            f'{self.id}'
            f", role='"
            f'{self.role}'
            f"', project_id="
            f'{self.project_id}'
            f')>'
        )
