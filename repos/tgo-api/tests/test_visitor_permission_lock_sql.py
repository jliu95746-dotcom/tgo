"""Typed row-lock targets must compile to the same PostgreSQL lock scope."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Query, Session

from app.core.exceptions import TGOAPIException
from app.models import Permission, ProjectRolePermission, RolePermission, Staff
from app.services import visitor_mutation_access


def compiled(query):
    statement = query.statement.compile(dialect=postgresql.dialect())
    return str(statement), statement.params


class LockQueries(Query):
    def first(self):
        return self.session.info["staff"]

    def all(self):
        self.session.info["statements"].append(compiled(self))
        entity = self.column_descriptions[0]["entity"]
        allowed = self.session.info["grant"]
        return [object()] if (
            entity is RolePermission and allowed == "global"
            or entity is ProjectRolePermission and allowed == "project"
        ) else []


@pytest.mark.parametrize("grant", ["global", "project", "absent"])
def test_permission_locks_keep_both_tables_and_project_filter(
    monkeypatch, grant,
):
    access = SimpleNamespace(staff_id=uuid4(), project_id=uuid4())
    staff = Staff(
        id=access.staff_id, project_id=access.project_id, role="user",
    )
    monkeypatch.setattr(
        visitor_mutation_access, "check_staff_record_permission",
        lambda *_: staff,
    )
    with Session(query_cls=LockQueries) as db:
        db.info.update(staff=staff, grant=grant, statements=[])
        expected = []
        for model in (RolePermission, ProjectRolePermission):
            query = db.query(model).join(Permission).filter(
                model.role == staff.role,
                Permission.resource == "visitors",
                Permission.action == "update",
            )
            if model is ProjectRolePermission:
                query = query.filter(model.project_id == access.project_id)
            expected.append(compiled(query.with_for_update(
                read=True, of=(model, Permission),
            )))
        if grant == "absent":
            with pytest.raises(TGOAPIException) as error:
                visitor_mutation_access.lock_permission(
                    db, access, "visitors:update",
                )
            assert error.value.status_code == 403
        else:
            assert visitor_mutation_access.lock_permission(
                db, access, "visitors:update",
            ) is staff
        actual = db.info["statements"]
        assert len(actual) == 2
        for (sql, parameters), (old_sql, old_parameters) in zip(
            actual, expected,
        ):
            # Predicate order does not change a conjunction, so compare the
            # full target/selection and every parameter independently of the
            # order in which the project predicate was supplied.
            assert sql.split("WHERE")[0] == old_sql.split("WHERE")[0]
            assert sql.split("FOR SHARE")[1] == old_sql.split("FOR SHARE")[1]
            assert parameters == old_parameters
            assert set(sql.split("WHERE")[1].split("FOR SHARE")[0]
                       .strip().split(" AND ")) == set(
                old_sql.split("WHERE")[1].split("FOR SHARE")[0]
                .strip().split(" AND ")
            )
