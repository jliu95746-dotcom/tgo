"""Opt-in model and session contracts against an owned PostgreSQL database."""

import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from src.rag_service import database
from src.rag_service.models import (
    Base,
    Collection,
    File,
    FileDocument,
    WebsitePage,
)
from src.rag_service.models.base import from_dict, to_dict


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("TGO_MODEL_TEST_DATABASE_URL"),
    reason="Requires an explicitly owned local model test database",
)
async def test_model_and_session_contracts_roundtrip(monkeypatch):
    url = os.environ["TGO_MODEL_TEST_DATABASE_URL"]
    parsed = make_url(url)
    assert parsed.host == "127.0.0.1"
    assert parsed.database and parsed.database.startswith("tgo_model_test_")
    settings = database.get_settings().model_copy(
        update={"database_url": url, "environment": "test"}
    )
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "engine", None)
    monkeypatch.setattr(database, "async_session_factory", None)
    engine = database.create_database_engine()
    initialized = False
    project, collection_id, file_id, document_id, page_id = [
        uuid4() for _ in range(5)
    ]
    metadata = {
        "enabled": True,
        "empty": None,
        "nested": {"items": [1, "资料", False]},
    }
    links = [{"url": "https://fixture.invalid/product", "created": False}]
    vector = [1.0] + [0.25] * 1535
    try:
        async with engine.begin() as connection:
            existing = await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_tables WHERE schemaname ="
                    " 'public'"
                )
            )
            assert (
                existing == 0
            ), "The explicitly owned test database must be empty"
            initialized = True
            await connection.execute(
                text("CREATE EXTENSION IF NOT EXISTS vector")
            )
            await connection.run_sync(Base.metadata.create_all)
        async with database.get_db_session() as session:
            collection = from_dict(
                Collection,
                {
                    "id": collection_id,
                    "project_id": project,
                    "display_name": "测试资料",
                    "collection_metadata": metadata,
                    "ignored_field": "not stored",
                },
            )
            collection.crawl_config = {"start_url": "https://fixture.invalid"}
            file = File(
                id=file_id,
                project_id=project,
                collection=collection,
                original_filename="fixture.txt",
                file_size=1,
                content_type="text/plain",
                storage_provider="local",
                storage_path="fixture-not-read.txt",
                storage_metadata=metadata,
                status="completed",
            )
            document = FileDocument(
                id=document_id,
                project_id=project,
                file=file,
                collection=collection,
                content="测试正文",
                tags=metadata,
                embedding=vector,
                embedding_model="fixture",
                embedding_dimensions=1536,
            )
            page = WebsitePage(
                id=page_id,
                project_id=project,
                file=file,
                collection=collection,
                url="https://fixture.invalid",
                url_hash="0" * 64,
                discovered_links=links,
                crawl_config={"max_depth": 2},
                page_metadata=metadata,
            )
            session.add_all([collection, file, document, page])
        assert database.async_session_factory is not None
        async with database.get_db_session() as session:
            loaded_file = await session.get(File, file_id)
            loaded_collection = await session.get(Collection, collection_id)
            loaded_document = await session.get(FileDocument, document_id)
            loaded_page = await session.get(WebsitePage, page_id)
            assert isinstance(loaded_file.project_id, UUID)
            assert loaded_file.project_id == project
            assert loaded_file.collection_id == collection_id
            assert loaded_file.storage_metadata == metadata
            assert loaded_collection.collection_metadata == metadata
            assert loaded_page.discovered_links == links
            assert loaded_page.page_metadata == metadata
            assert loaded_document.tags == metadata
            assert list(loaded_document.embedding) == vector
            serialized = to_dict(loaded_collection, exclude={"updated_at"})
            assert serialized["id"] == str(collection_id)
            assert serialized["collection_metadata"] == metadata
            assert isinstance(serialized["created_at"], str)
            assert "updated_at" not in serialized
        # A failed session must roll back its changes and leave a fresh session
        # usable.
        with pytest.raises(RuntimeError, match="fixture rollback"):
            async with database.get_db_session() as session:
                loaded = await session.get(Collection, collection_id)
                loaded.display_name = "should roll back"
                await session.flush()
                raise RuntimeError("fixture rollback")
        async with database.get_db_session() as session:
            loaded = await session.get(Collection, collection_id)
            assert loaded.display_name == "测试资料"
            loaded.soft_delete()
        async with database.get_db_session() as session:
            visible = (
                await session.execute(
                    select(Collection).where(
                        Collection.id == collection_id,
                        Collection.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            assert visible is None
            loaded = await session.get(Collection, collection_id)
            loaded.restore()
    finally:
        if initialized:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.drop_all)
        await database.close_database()
