"""Real file parsing, Celery and pgvector retrieval in private SQL."""

import asyncio
import importlib.util
import logging
import sys
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from langchain_core.embeddings import Embeddings
from langchain_postgres import PGEngine, PGVectorStore
from sqlalchemy import select, text
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "repos/tgo-rag"))
from src.rag_service.models import (  # noqa: E402
    Collection,
    CollectionType,
    File,
    FileDocument,
)
from src.rag_service.services import search, vector_store  # noqa: E402
from src.rag_service.tasks import (  # noqa: E402
    document_embedding,
    document_processing_core,
    document_processing_errors,
)
from src.rag_service.tasks.document_processing import (  # noqa: E402
    process_file_task,
)

spec = importlib.util.spec_from_file_location(
    "queue_verification",
    Path(__file__).with_name("qa-worker-isolation-e2e.py"),
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class TestEmbeddings(Embeddings):
    def __init__(self, index):
        self.index = index

    def embed_documents(self, texts):
        return [self.embed_query(value) for value in texts]

    def embed_query(self, text):
        vector = [0.0] * 1536
        vector[self.index] = 1.0
        return vector


class FilePipelineVerification(fixture.Verification):
    def __init__(self):
        super().__init__()
        self.file_ids = [uuid4(), uuid4()]
        self.directory = (
            ROOT / ".tmp" / ("file-pipeline-" + self.marker)
        ).resolve()
        self.paths = [self.directory / f"company-{i}.txt" for i in range(2)]
        self.pg_engine = None
        self.stores = {}
        self.vector_service = vector_store.VectorStoreService()
        self.vector_service.get_vector_store_for_project = self.get_store

    async def seed(self):
        self.directory.mkdir()
        engine = self.engine()
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(f'CREATE SCHEMA "{self.schema}"')
                )
                await connection.execute(
                    text(
                        f'COMMENT ON SCHEMA "{self.schema}" '
                        f"IS '{self.marker}'"
                    )
                )
                for model in (Collection, File, FileDocument):
                    await connection.run_sync(model.__table__.create)
            async with self.session() as db:
                for index in range(2):
                    db.add(
                        Collection(
                            id=self.collections[index],
                            project_id=self.projects[index],
                            display_name=f"Synthetic upload {index}",
                            collection_type=CollectionType.file,
                        )
                    )
                await db.flush()
                for index, path in enumerate(self.paths):
                    path.write_text(
                        (
                            f"Private company-{index} record-{self.marker}.\n"
                            * 60
                        ),
                        encoding="utf-8",
                    )
                    db.add(
                        File(
                            id=self.file_ids[index],
                            project_id=self.projects[index],
                            collection_id=self.collections[index],
                            original_filename=path.name,
                            content_type="text/plain",
                            file_size=path.stat().st_size,
                            storage_provider="local",
                            storage_path=str(path),
                            status="pending",
                        )
                    )
                await db.commit()
        finally:
            await engine.dispose()

    async def embedding_service(self, project_id):
        index = [str(p) for p in self.projects].index(str(project_id))
        return SimpleNamespace(embeddings_client=TestEmbeddings(index))

    async def get_store(self, project_key, embedding_client):
        assert project_key in [str(p) for p in self.projects]
        if self.pg_engine is None:
            self.pg_engine = PGEngine.from_connection_string(
                self.settings.database_url,
                poolclass=NullPool,
                connect_args={
                    "server_settings": {"search_path": f"{self.schema},public"}
                },
            )
        if project_key not in self.stores:
            self.stores[project_key] = await PGVectorStore.create(
                engine=self.pg_engine,
                embedding_service=embedding_client,
                table_name="rag_file_documents",
                schema_name=self.schema,
                id_column="id",
                content_column="content",
                metadata_columns=["file_id", "collection_id", "project_id"],
            )
        return self.stores[project_key]

    async def check_persistence_and_search(self):
        service = search.SearchService()
        service.vector_store_service = self.vector_service
        for index, project_id in enumerate(self.projects):
            async with self.session() as db:
                file = await db.get(File, self.file_ids[index])
                assert file.status == "completed"
                rows = (
                    (
                        await db.execute(
                            select(FileDocument).where(
                                FileDocument.file_id == file.id,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(rows) >= 2 and file.document_count == len(rows)
                assert file.total_tokens > 0
                assert all(row.project_id == project_id for row in rows)
                assert all(
                    row.collection_id == self.collections[index]
                    for row in rows
                )
                assert all(len(row.embedding) == 1536 for row in rows)
                expected_ids = {row.id for row in rows}
            # Other-company query words must still return only owned data.
            result = await service.semantic_search(
                query=f"Private company-{1-index}",
                project_id=project_id,
                collection_id=self.collections[index],
                limit=100,
                filters={"project_id": str(self.projects[1 - index])},
            )
            assert {row.document_id for row in result.results} == expected_ids
            assert all(
                row.file_id == self.file_ids[index] for row in result.results
            )
            assert all(
                f"company-{index}" in row.content_preview
                for row in result.results
            )
            foreign = await service.semantic_search(
                query="Private",
                project_id=project_id,
                collection_id=self.collections[1 - index],
                limit=100,
            )
            assert foreign.results == []
        print(
            "PASS: real chunk rows, vectors and semantic results "
            "remain tenant-bound",
            flush=True,
        )

    def run_checks(self):
        for index in range(2):
            result = process_file_task.apply_async(
                args=[
                    str(self.file_ids[index]),
                    str(self.collections[index]),
                    False,
                ],
                queue=self.queue,
                exchange=self.queue,
                routing_key=self.queue,
            )
            self.results.append(result)
            response = result.get(timeout=90, disable_sync_subtasks=False)
            assert response["status"] == "completed", response
            print(
                f"PASS: company-{index} file parsed, chunked and indexed "
                "by real worker",
                flush=True,
            )
        asyncio.run(self.check_persistence_and_search())

    def run(self):
        try:
            with ExitStack() as replacements:
                for module in (
                    document_processing_core,
                    document_processing_errors,
                    search,
                ):
                    replacements.enter_context(
                        patch.object(module, "get_db_session", self.session)
                    )
                for module in (document_embedding, search):
                    replacements.enter_context(
                        patch.object(
                            module,
                            "get_embedding_service_for_project",
                            self.embedding_service,
                        )
                    )
                replacements.enter_context(
                    patch.object(
                        document_embedding,
                        "get_vector_store_service",
                        lambda: self.vector_service,
                    )
                )
                super().run()
        finally:
            if self.pg_engine:
                asyncio.run(self.pg_engine.close())
            assert self.directory.parent == (ROOT / ".tmp").resolve()
            for path in self.paths:
                if path.is_file():
                    assert path.parent == self.directory
                    path.unlink()
            if self.directory.exists():
                self.directory.rmdir()
            print(
                "PASS: owned input files removed; "
                "private vector connection closed",
                flush=True,
            )


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    FilePipelineVerification().run()
