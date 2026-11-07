"""Generic CRUD keeps schema compatibility and project filtering."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.core.exceptions import NotFoundError
from app.services.base import BaseService


class IsolatedBase(DeclarativeBase):
    pass


class Record(IsolatedBase):
    __tablename__ = "generic_service_record"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID]
    name: Mapped[str]
    deleted_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime(2026, 1, 1)
    )


class PlainRecord(IsolatedBase):
    __tablename__ = "generic_service_plain_record"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]


class ConstantIdentityRecord(IsolatedBase):
    __tablename__ = "generic_service_constant_identity"
    key: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    id = 1


class CreateRecord(BaseModel):
    project_id: UUID
    name: str


class UpdateRecord(BaseModel):
    name: str | None = None
    ignored: str | None = None


class LegacyCreate:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def dict(self):
        self.calls.append("dict")
        return self.data


class ModernCreate:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def model_dump(self):
        self.calls.append("model_dump")
        return self.data


class LegacyUpdate:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def dict(self, *, exclude_unset):
        self.calls.append(exclude_unset)
        return self.data


class ModernUpdate:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def model_dump(self, *, exclude_unset):
        self.calls.append(exclude_unset)
        return self.data


@pytest.fixture
def records():
    engine = create_engine("sqlite://")
    IsolatedBase.metadata.create_all(engine)
    with Session(engine) as db:
        project, other = uuid4(), uuid4()
        rows = [
            Record(project_id=project, name="owned"),
            Record(project_id=project, name="archived",
                   deleted_at=datetime(2026, 1, 2)),
            Record(project_id=other, name="foreign"),
        ]
        db.add_all(rows)
        db.commit()
        yield db, rows, project, other
    engine.dispose()


@pytest.mark.parametrize("kind", ["pydantic", "legacy", "modern_only"])
def test_create_keeps_modern_and_legacy_no_argument_serialization(
    records, kind,
):
    db, _, project, _ = records
    data = {"project_id": project, "name": "created"}
    schema = {
        "pydantic": lambda: CreateRecord(**data),
        "legacy": lambda: LegacyCreate(data),
        "modern_only": lambda: ModernCreate(data),
    }[kind]()
    service = BaseService(Record)
    result = service.create(db, obj_in=schema)
    assert result.id is not None
    assert db.get(Record, result.id) is result
    assert result.name == "created" and result.project_id == project
    if kind != "pydantic":
        assert len(schema.calls) == 1


@pytest.mark.parametrize("kind", ["pydantic", "legacy", "modern_only"])
def test_update_excludes_unset_fields_and_ignores_unknown_attributes(
    records, kind,
):
    db, rows, _, _ = records
    data = {"name": "updated", "ignored": "not-a-column"}
    schema = {
        "pydantic": lambda: UpdateRecord(**data),
        "legacy": lambda: LegacyUpdate(data),
        "modern_only": lambda: ModernUpdate(data),
    }[kind]()
    result = BaseService(Record).update(db, db_obj=rows[0], obj_in=schema)
    assert result.name == "updated"
    assert result.updated_at > datetime(2026, 1, 1)
    assert not hasattr(result, "ignored")
    if kind != "pydantic":
        assert schema.calls == [True]


def test_update_does_not_write_unset_optional_fields(records):
    db, rows, _, _ = records
    result = BaseService(Record).update(
        db, db_obj=rows[0], obj_in=UpdateRecord(ignored="value"),
    )
    assert result.name == "owned"


def test_generic_and_project_queries_preserve_different_delete_scopes(records):
    db, rows, project, other = records
    service = BaseService(Record)
    assert service.get(db, rows[1].id) is rows[1]
    assert service.get_or_404(db, rows[0].id) is rows[0]
    assert service.get(db, uuid4()) is None
    with pytest.raises(NotFoundError):
        service.get_or_404(db, uuid4())
    assert service.count(db) == 3
    assert {r.name for r in service.get_multi(db)} == {
        "owned", "archived", "foreign",
    }
    assert service.get_by_project(db, project_id=project) == [rows[0]]
    assert service.count_by_project(db, project_id=project) == 1
    assert service.get_by_project(db, project_id=other) == [rows[2]]


def test_filters_skip_none_and_unknown_names_and_pagination_is_preserved(
    records,
):
    db, rows, project, _ = records
    service = BaseService(Record)
    ignored = {"missing": "ignored", "name": None}
    assert service.count(db, ignored) == 3
    assert len(service.get_multi(db, skip=1, limit=1, filters=ignored)) == 1
    assert service.get_multi(db, filters={"name": "foreign"}) == [rows[2]]
    assert service.count(db, {"name": "foreign"}) == 1
    assert service.get_by_project(
        db, project_id=project, filters={"name": "foreign"},
    ) == []
    assert service.count_by_project(
        db, project_id=project, filters={"name": "foreign"},
    ) == 0


def test_soft_delete_keeps_row_and_hard_delete_removes_row(records):
    db, rows, project, _ = records
    service = BaseService(Record)
    deleted = service.soft_delete(db, id=rows[0].id)
    assert deleted.deleted_at is not None
    assert service.get(db, deleted.id) is deleted
    assert service.count_by_project(db, project_id=project) == 0
    removed = service.remove(db, id=rows[2].id)
    assert db.get(Record, removed.id) is None
    for method in (service.remove, service.soft_delete):
        with pytest.raises(NotFoundError):
            method(db, id=uuid4())


def test_models_without_project_or_soft_delete_columns_keep_legacy_fallback(
    records,
):
    db, _, project, _ = records
    row = PlainRecord(id=1, name="plain")
    db.add(row)
    db.commit()
    service = BaseService(PlainRecord)
    assert service.get_by_project(db, project_id=project) == [row]
    assert service.count_by_project(db, project_id=project) == 1
    assert service.soft_delete(db, id=row.id).id == 1
    assert db.get(PlainRecord, row.id) is None


def test_unmapped_identity_cannot_turn_an_id_lookup_into_an_unscoped_query(
    records,
):
    db, _, _, _ = records
    db.add(ConstantIdentityRecord(key=42, name="not-the-requested-identity"))
    db.commit()
    with pytest.raises(TypeError, match="not a mapped field"):
        BaseService(ConstantIdentityRecord).get(db, 1)
