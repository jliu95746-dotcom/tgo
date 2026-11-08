"""History remains readable while invalid snapshots cannot become live data."""

from datetime import UTC, datetime
from copy import deepcopy
import json
import os
from uuid import UUID, uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio
from sqlalchemy import select

from src.rag_service.database import get_db_session_dependency
from src.rag_service.models import FileDocument
from src.rag_service.models.knowledge_versions import KnowledgeVersion
from src.rag_service.routers.knowledge_versions import router
from src.rag_service.schemas.knowledge_versions import (
    VersionChangeRequest,
    VersionInput,
    VersionSnapshot,
)
from src.rag_service.services import knowledge_version_prepare as preparation
from src.rag_service.services.knowledge_version_publish import publish
from src.rag_service.services.knowledge_versions import (
    create_change,
    version_response,
)

# Reuse the owned PostgreSQL/schema isolation fixtures, without copying them.
from tests import test_knowledge_version_recovery as recovery

version_database = recovery.version_database
version_store = recovery.version_store


PRIVATE = "fixture-private-snapshot-value"
INVALID_SNAPSHOTS = [
    pytest.param(None, id="json-null"),
    pytest.param([PRIVATE], id="json-array"),
    pytest.param(PRIVATE, id="json-string"),
    pytest.param({"content": {"question": [PRIVATE]}}, id="invalid-question"),
    pytest.param({"chunks": PRIVATE}, id="invalid-chunks"),
    pytest.param({"attempt": PRIVATE}, id="invalid-attempt"),
]
NATIVE = pytest.mark.skipif(
    not os.getenv("TGO_VERSION_PREPARE_TEST_DATABASE_URL"),
    reason="Requires owned version preparation PostgreSQL database",
)


def row(snapshot, state="ready"):
    return KnowledgeVersion(
        id=uuid4(),
        source_id=uuid4(),
        number=3,
        state=state,
        author="原员工",
        snapshot=snapshot,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        published_by="原审核人",
        published_at=datetime(2026, 1, 2, tzinfo=UTC),
        restored_from=1,
        error=PRIVATE,
    )


@pytest.mark.parametrize("snapshot", INVALID_SNAPSHOTS)
def test_bad_history_record_preserves_metadata_and_hides_invalid_body(
    snapshot,
):
    version = row(snapshot)
    result = version_response(version)
    assert result.id == version.id
    assert result.number == 3 and result.state == "ready"
    assert result.author == "原员工" and result.published_by == "原审核人"
    assert result.created_at == version.created_at
    assert result.published_at == version.published_at
    assert result.restored_from == 1
    assert result.content == VersionInput() and result.preview == ""
    assert result.error and PRIVATE not in result.model_dump_json()
    assert version.snapshot == snapshot and version.error == PRIVATE


@pytest.mark.parametrize(
    "state",
    [
        "processing",
        "ready",
        "pending_review",
        "published",
        "retired",
        "failed",
        "unchanged",
    ],
)
def test_bad_history_record_never_invents_a_different_persisted_state(state):
    version = row({"attempt": PRIVATE}, state)
    assert version_response(version).state == state
    assert version.state == state


def test_valid_history_retains_public_fields_without_internal_vectors():
    text = "可用资料" * 3000
    snapshot = {
        "attempt": str(uuid4()),
        "content": {"question": "保修多久？", "answer": "一年"},
        "storage_path": "fixture-private-path",
        "chunks": [
            {
                "content": text,
                "embedding": [0.1] * 1536,
                "embedding_model": "fixture-private-model",
            },
            {
                "content": "尾段",
                "embedding": [0.2] * 1536,
                "embedding_model": "fixture-private-model",
            },
        ],
    }
    version = row(snapshot)
    result = version_response(version)
    assert result.error == PRIVATE
    assert result.content == VersionInput(question="保修多久？", answer="一年")
    assert result.preview == (text + "\n\n尾段")[:8000]
    assert result.preview and len(result.preview) == 8000
    payload = result.model_dump_json()
    assert "embedding" not in payload and "fixture-private-path" not in payload
    assert "fixture-private-model" not in payload
    assert version.snapshot is snapshot


@pytest_asyncio.fixture
async def history_client(version_store):
    application = FastAPI()
    application.include_router(router, prefix="/versions")

    async def database():
        async with version_store.factory() as db:
            yield db

    application.dependency_overrides[get_db_session_dependency] = database
    async with AsyncClient(
        transport=ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://fixture",
    ) as client:
        yield client


async def corrupt(store, identifier, snapshot, state=None):
    async with store.factory() as db:
        revision = await db.get(KnowledgeVersion, identifier)
        revision.snapshot = snapshot
        revision.error = PRIVATE
        if state:
            revision.state = state
        await db.commit()


async def activate(store, case):
    assert (
        await preparation.process_revision(
            case.project, case.revision, case.attempt
        )
        == "ready"
    )
    async with store.factory() as db:
        await publish(db, case.project, case.revision, "测试管理员")
        await db.commit()


async def retained(store, case):
    async with store.factory() as db:
        rows = (
            await db.scalars(
                select(KnowledgeVersion)
                .where(KnowledgeVersion.source_id == case.source)
                .order_by(KnowledgeVersion.number)
            )
        ).all()
        return rows


@NATIVE
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["qa", "file", "website"])
async def test_history_http_keeps_other_versions_readable(
    version_store, history_client, kind
):
    case = await version_store.make(kind)
    await corrupt(version_store, case.revision, {"attempt": PRIVATE})
    response = await history_client.get(
        f"/versions/{kind}/{case.parent}",
        params={"project_id": str(case.project)},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["active_number"] == 1 and not result["disabled"]
    assert result["retention"] == 10
    assert [item["number"] for item in result["versions"]] == [2, 1]
    draft, live = result["versions"]
    assert draft["state"] == "processing" and draft["error"]
    assert draft["preview"] == "" and draft["content"]["answer"] is None
    assert live["state"] == "published" and live["preview"]
    assert PRIVATE not in response.text
    revision, source, documents = await version_store.read(case)
    assert revision.snapshot == {"attempt": PRIVATE}
    assert revision.state == "processing" and revision.error == PRIVATE
    assert source.active_number == 1
    assert [item.id for item in documents] == [case.document]


@NATIVE
@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", INVALID_SNAPSHOTS)
@pytest.mark.parametrize("operation", ["publish", "submit"])
async def test_invalid_draft_operation_is_409_without_changing_live_data(
    version_store, history_client, snapshot, operation
):
    case = await version_store.make()
    await corrupt(version_store, case.revision, snapshot, "ready")
    response = await history_client.post(
        f"/versions/versions/{case.revision}/{operation}",
        params={"project_id": str(case.project)},
        json={"actor": "测试管理员"},
    )
    assert response.status_code == 409, response.text
    assert PRIVATE not in response.text
    revision, source, documents = await version_store.read(case)
    assert revision.state == "ready" and revision.snapshot == snapshot
    assert source.active_number == 1 and not source.disabled
    assert [item.id for item in documents] == [case.document]
    assert documents[0].content.endswith("一年")
    assert [item.state for item in await retained(version_store, case)] == [
        "published",
        "ready",
    ]


@NATIVE
@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", INVALID_SNAPSHOTS)
async def test_invalid_restore_keeps_pending_history_and_current_answer(
    version_store, history_client, snapshot
):
    case = await version_store.make()
    await activate(version_store, case)
    previous = (await retained(version_store, case))[0]
    async with version_store.factory() as db:
        marker = KnowledgeVersion(
            source_id=case.source,
            number=3,
            state="unchanged",
            author="测试员工",
            snapshot=VersionSnapshot().model_dump(mode="json"),
        )
        db.add(marker)
        await db.commit()
        marker_id = marker.id
    await corrupt(version_store, previous.id, snapshot)
    response = await history_client.post(
        f"/versions/versions/{previous.id}/restore",
        params={"project_id": str(case.project)},
        json={"actor": "测试管理员"},
    )
    assert response.status_code == 409, response.text
    assert PRIVATE not in response.text
    rows = await retained(version_store, case)
    assert [item.number for item in rows] == [1, 2, 3]
    assert rows[0].snapshot == snapshot and rows[0].state == "retired"
    assert rows[2].id == marker_id and rows[2].state == "unchanged"
    _, source, documents = await version_store.read(case)
    assert source.active_number == 2
    assert len(documents) == 1 and documents[0].content.endswith("两年")


@NATIVE
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "vector", [[0.0] * 1536, [0.1], [float("nan")] * 1536]
)
@pytest.mark.parametrize("operation", ["publish", "submit"])
async def test_unusable_vectors_cannot_be_submitted_or_published(
    version_store, history_client, vector, operation
):
    case = await version_store.make()
    assert (
        await preparation.process_revision(
            case.project, case.revision, case.attempt
        )
        == "ready"
    )
    async with version_store.factory() as db:
        version = await db.get(KnowledgeVersion, case.revision)
        snapshot = deepcopy(version.snapshot)
        snapshot["chunks"][0]["embedding"] = vector
        # PostgreSQL JSON rejects NaN; use its JSON string representation,
        # which Pydantic converts back into a nonfinite float.
        if any(value != value for value in vector):
            snapshot["chunks"][0]["embedding"] = ["nan"] * 1536
        version.snapshot = snapshot
        await db.commit()
    async with version_store.factory() as db:
        stored = await db.get(KnowledgeVersion, case.revision)
        assert stored.snapshot["chunks"][0]["embedding"] == (
            snapshot["chunks"][0]["embedding"]
        )
    response = await history_client.post(
        f"/versions/versions/{case.revision}/{operation}",
        params={"project_id": str(case.project)},
        json={"actor": "测试管理员"},
    )
    assert response.status_code == 409, response.text
    assert (await version_store.read(case))[1].active_number == 1


@NATIVE
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", ["publish", "restore", "submit", "discard"]
)
async def test_invalid_snapshot_does_not_bypass_project_ownership(
    version_store, history_client, operation
):
    case = await version_store.make()
    await corrupt(version_store, case.revision, {"attempt": PRIVATE}, "ready")
    response = await history_client.post(
        f"/versions/versions/{case.revision}/{operation}",
        params={"project_id": str(uuid4())},
        json={"actor": "其他企业员工"},
    )
    assert response.status_code == 404 and PRIVATE not in response.text
    assert (await version_store.read(case))[0].state == "ready"


@NATIVE
@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["publish", "submit", "restore"])
@pytest.mark.parametrize(
    "problem", ["question", "answer", "blank-question", "multiple-chunks"]
)
async def test_incomplete_qa_cannot_enter_review_or_replace_live_content(
    version_store, history_client, operation, problem
):
    case = await version_store.make()
    assert (
        await preparation.process_revision(
            case.project, case.revision, case.attempt
        )
        == "ready"
    )
    async with version_store.factory() as db:
        revision = await db.get(KnowledgeVersion, case.revision)
        snapshot = deepcopy(revision.snapshot)
        if problem == "multiple-chunks":
            snapshot["chunks"].append(deepcopy(snapshot["chunks"][0]))
        elif problem == "blank-question":
            snapshot["content"]["question"] = "   "
        else:
            snapshot["content"][problem] = None
        revision.snapshot = snapshot
        if operation == "restore":
            revision.state = "retired"
        await db.commit()
    response = await history_client.post(
        f"/versions/versions/{case.revision}/{operation}",
        params={"project_id": str(case.project)},
        json={"actor": "测试管理员"},
    )
    assert response.status_code == 409, response.text
    revision, source, documents = await version_store.read(case)
    assert source.active_number == 1 and documents[0].id == case.document
    assert revision.state == ("retired" if operation == "restore" else "ready")
    assert len(await retained(version_store, case)) == 2


@NATIVE
@pytest.mark.asyncio
async def test_corrupt_draft_can_be_discarded_without_touching_live_answer(
    version_store, history_client
):
    case = await version_store.make()
    await corrupt(version_store, case.revision, None, "ready")
    response = await history_client.post(
        f"/versions/versions/{case.revision}/discard",
        params={"project_id": str(case.project)},
        json={"actor": "测试管理员"},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["state"] == "unchanged" and result["error"] is None
    assert result["preview"] == ""
    revision, source, documents = await version_store.read(case)
    assert revision.snapshot == VersionSnapshot().model_dump(mode="json")
    assert source.active_number == 1 and documents[0].id == case.document


@NATIVE
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "snapshot", [None, [], PRIVATE, {"storage_path": 123}]
)
async def test_confirmed_history_cleanup_handles_invalid_snapshot_shapes(
    version_store, history_client, snapshot
):
    case = await version_store.make()
    await activate(version_store, case)
    async with version_store.factory() as db:
        revision = await create_change(
            db,
            case.project,
            "qa",
            case.parent,
            VersionChangeRequest(
                actor="测试员工", content=VersionInput(answer="三年")
            ),
        )
        identifier, attempt = revision.id, revision.snapshot["attempt"]
        await db.commit()
    assert (
        await preparation.process_revision(
            case.project, identifier, UUID(attempt)
        )
        == "ready"
    )
    async with version_store.factory() as db:
        await publish(db, case.project, identifier, "测试管理员")
        await db.commit()
    oldest = (await retained(version_store, case))[0]
    await corrupt(version_store, oldest.id, snapshot)
    response = await history_client.post(
        f"/versions/qa/{case.parent}/cleanup",
        params={"project_id": str(case.project)},
        json={"retention": 2, "confirm": True, "expected_numbers": [1]},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "removable_numbers": [1],
        "removed": 1,
        "file_cleanup_pending": 0,
    }
    assert [item.number for item in await retained(version_store, case)] == [
        2,
        3,
    ]
    async with version_store.factory() as db:
        documents = (
            await db.scalars(
                select(FileDocument).where(
                    FileDocument.collection_id == case.collection
                )
            )
        ).all()
        assert len(documents) == 1 and documents[0].content.endswith("三年")


@NATIVE
@pytest.mark.asyncio
async def test_valid_submit_publish_and_restore_http_keep_original_behavior(
    version_store, history_client
):
    case = await version_store.make()
    assert (
        await preparation.process_revision(
            case.project, case.revision, case.attempt
        )
        == "ready"
    )
    original = (await retained(version_store, case))[0]
    for operation, identifier, expected_state, expected_answer in [
        ("submit", case.revision, "pending_review", "两年"),
        ("publish", case.revision, "published", "两年"),
        ("restore", original.id, "published", "一年"),
    ]:
        response = await history_client.post(
            f"/versions/versions/{identifier}/{operation}",
            params={"project_id": str(case.project)},
            json={"actor": "测试管理员"},
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["state"] == expected_state
        assert result["content"]["answer"] == expected_answer
        assert result["error"] is None
        assert "embedding" not in json.dumps(result)
    assert (await version_store.read(case))[1].active_number == 3
