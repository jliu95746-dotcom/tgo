"""Exercise table initialization against the installed PGVector SQL builder."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from langchain_postgres import PGEngine
from sqlalchemy.exc import ProgrammingError

from src.rag_service import database
from src.rag_service.services import vector_store


class DriverFailure(Exception):
    def __init__(self, sqlstate):
        self.sqlstate = sqlstate
        super().__init__("fixture driver detail not safe for logs")


@pytest.fixture
def initialized_service(monkeypatch):
    monkeypatch.setattr(
        vector_store,
        "get_embedding_service",
        lambda: SimpleNamespace(embeddings_client=Mock()),
    )
    engine = SimpleNamespace(
        init_vectorstore_table=Mock(),
        ainit_vectorstore_table=AsyncMock(),
        close=AsyncMock(),
    )
    from_connection = Mock(return_value=engine)
    monkeypatch.setattr(
        vector_store.PGEngine, "from_connection_string", from_connection
    )
    created_store = SimpleNamespace()
    create_store = AsyncMock(return_value=created_store)
    monkeypatch.setattr(vector_store.PGVectorStore, "create", create_store)
    return (
        vector_store.VectorStoreService(),
        engine,
        create_store,
        from_connection,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("project", [False, True])
async def test_metadata_columns_are_in_real_generated_sql(
    initialized_service, project
):
    service, engine, create_store, _ = initialized_service
    if project:
        await service.get_vector_store_for_project("fixture-project", Mock())
    else:
        await service.get_vector_store()
    # The native PGEngine builds the DDL; this does not duplicate its rules.
    sql = []

    class Connection:
        async def execute(self, statement):
            sql.append(str(statement))

        async def commit(self):
            pass

    class Pool:
        @asynccontextmanager
        async def connect(self):
            yield Connection()

    real_builder = object.__new__(PGEngine)
    real_builder._pool = Pool()
    arguments = (
        engine.ainit_vectorstore_table.call_args
        or engine.init_vectorstore_table.call_args
    ).kwargs
    await real_builder._ainit_vectorstore_table(**arguments)
    ddl = next(
        statement for statement in sql if statement.startswith("CREATE TABLE")
    )
    assert '"file_id" UUID' in ddl
    assert '"collection_id" UUID' in ddl
    assert '"project_id" UUID NOT NULL' in ddl
    assert create_store.call_args.kwargs["metadata_columns"] == [
        "file_id",
        "collection_id",
        "project_id",
    ]
    engine.init_vectorstore_table.assert_not_called()
    engine.ainit_vectorstore_table.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("project", [False, True])
@pytest.mark.parametrize("sqlstate", ["42501", "42601", None])
async def test_only_duplicate_table_can_be_ignored(
    initialized_service, monkeypatch, project, sqlstate
):
    service, engine, create_store, _ = initialized_service
    logger = Mock()
    monkeypatch.setattr(vector_store, "logger", logger)
    error = ProgrammingError("fixture statement", {}, DriverFailure(sqlstate))
    engine.init_vectorstore_table.side_effect = error
    engine.ainit_vectorstore_table.side_effect = error
    with pytest.raises(ProgrammingError) as raised:
        if project:
            await service.get_vector_store_for_project(
                "fixture-project", Mock()
            )
        else:
            await service.get_vector_store()
    assert raised.value is error
    create_store.assert_not_awaited()
    assert service._pg_engine is None
    engine.close.assert_awaited_once()
    assert "fixture driver detail" not in repr(logger.mock_calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("project", [False, True])
async def test_existing_table_continues_and_engine_is_reused(
    initialized_service, project
):
    service, engine, create_store, from_connection = initialized_service
    error = ProgrammingError("fixture", {}, DriverFailure("42P07"))
    engine.init_vectorstore_table.side_effect = error
    engine.ainit_vectorstore_table.side_effect = error
    if project:
        first = await service.get_vector_store_for_project("a", Mock())
        assert await service.get_vector_store_for_project("a", Mock()) is first
        await service.get_vector_store_for_project("b", Mock())
        assert create_store.await_count == 2
    else:
        first = await service.get_vector_store()
        assert await service.get_vector_store() is first
        create_store.assert_awaited_once()
    from_connection.assert_called_once()
    assert service._pg_engine is engine
    engine.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_embedding_info_uses_newly_created_session_factory(monkeypatch):
    service = vector_store.VectorStoreService()
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalar_one_or_none=lambda: SimpleNamespace()
            )
        ),
        commit=AsyncMock(),
        rollback=AsyncMock(),
        close=AsyncMock(),
    )

    @asynccontextmanager
    async def session_factory():
        yield session

    create_factory = Mock(return_value=session_factory)
    monkeypatch.setattr(database, "async_session_factory", None)
    monkeypatch.setattr(database, "create_session_factory", create_factory)
    service.embedding_service = SimpleNamespace(
        get_embedding_model=lambda: "fixture",
        get_embedding_dimensions=lambda: 2,
    )
    await service._update_document_embedding_info(uuid4(), [0.25, 0.75])
    create_factory.assert_called_once()
    assert session.execute.await_count == 2
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_initialization_creates_one_engine(
    initialized_service,
):
    service, engine, _, from_connection = initialized_service
    entered, release = asyncio.Event(), asyncio.Event()

    async def initialize(**kwargs):
        entered.set()
        await release.wait()

    engine.ainit_vectorstore_table.side_effect = initialize
    first = asyncio.create_task(service._get_pg_engine())
    second = asyncio.create_task(service._get_pg_engine())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert not first.done() and not second.done()
    finally:
        release.set()
    assert await first is engine
    assert await second is engine
    from_connection.assert_called_once()
    engine.ainit_vectorstore_table.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_initialization_does_not_cache_partial_engine(
    initialized_service,
):
    service, engine, _, from_connection = initialized_service
    engine.ainit_vectorstore_table.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await service._get_pg_engine()
    assert service._pg_engine is None
    engine.close.assert_awaited_once()
    engine.ainit_vectorstore_table.side_effect = None
    assert await service._get_pg_engine() is engine
    assert from_connection.call_count == 2


@pytest.mark.asyncio
async def test_cleanup_failure_preserves_original_error(
    initialized_service, monkeypatch
):
    service, engine, _, _ = initialized_service
    logger = Mock()
    monkeypatch.setattr(vector_store, "logger", logger)
    failure = ProgrammingError("fixture", {}, DriverFailure("42501"))
    engine.ainit_vectorstore_table.side_effect = failure
    engine.close.side_effect = RuntimeError("fixture private close detail")
    with pytest.raises(ProgrammingError) as raised:
        await service._get_pg_engine()
    assert raised.value is failure
    assert service._pg_engine is None
    assert "fixture private" not in repr(logger.mock_calls)
