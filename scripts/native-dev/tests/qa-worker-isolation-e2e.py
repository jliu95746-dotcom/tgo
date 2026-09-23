"""Real Redis/Celery QA processing with test vectors and private SQL."""

import asyncio
import logging
import re
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import redis
from celery.contrib.testing.worker import start_worker
from kombu import Exchange, Queue
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "repos/tgo-rag"))
from src.rag_service.config import get_settings  # noqa: E402
from src.rag_service.models import (  # noqa: E402
    Collection,
    CollectionType,
    File,
    FileDocument,
    QAPair,
)
from src.rag_service.schemas.qa import compute_question_hash  # noqa: E402
from src.rag_service.services import qa_documents  # noqa: E402
from src.rag_service.tasks.celery_app import celery_app  # noqa: E402
from src.rag_service.tasks.qa_processing import (  # noqa: E402
    process_qa_pair_task,
)


class Verification:
    def __init__(self):
        self.settings = get_settings()
        self.marker = uuid4().hex
        self.schema = "qa_worker_verify_" + self.marker
        self.queue = "qa-worker-verify-" + self.marker
        self.projects = [uuid4(), uuid4()]
        self.collections = [uuid4(), uuid4()]
        self.pairs = [uuid4(), uuid4()]
        self.embedded_projects = []
        self.results = []

    def engine(self):
        return create_async_engine(
            self.settings.database_url,
            poolclass=NullPool,
            connect_args={
                "server_settings": {
                    "search_path": f"{self.schema},public",
                }
            },
        )

    @asynccontextmanager
    async def session(self):
        engine = self.engine()
        try:
            async with AsyncSession(engine, expire_on_commit=False) as db:
                # Refuse all reads/writes if the private schema disappeared.
                active = await db.scalar(text("SELECT current_schema()"))
                assert active == self.schema, "Private schema is unavailable"
                yield db
        finally:
            await engine.dispose()

    async def seed(self):
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
                for model in (Collection, File, FileDocument, QAPair):
                    await connection.run_sync(model.__table__.create)
            async with self.session() as db:
                for index in range(2):
                    db.add(
                        Collection(
                            id=self.collections[index],
                            project_id=self.projects[index],
                            display_name=f"Synthetic company {index}",
                            collection_type=CollectionType.qa,
                        )
                    )
                await db.flush()
                for index in range(2):
                    question = f"Synthetic question {index}"
                    db.add(
                        QAPair(
                            id=self.pairs[index],
                            collection_id=self.collections[index],
                            project_id=self.projects[index],
                            question=question,
                            answer=f"Synthetic private answer {index}",
                            question_hash=compute_question_hash(question),
                            status="pending",
                        )
                    )
                await db.commit()
        finally:
            await engine.dispose()

    async def embedding_service(self, project_id):
        assert project_id in self.projects
        index = self.projects.index(project_id)
        self.embedded_projects.append(project_id)

        class Embedding:
            async def generate_embedding(inner_self, content):
                assert f"Synthetic private answer {index}" in content
                vector = [0.0] * 1536
                vector[index] = 1.0
                return vector

            def get_embedding_model(inner_self):
                return "synthetic-isolation-verification"

        return Embedding()

    def submit(self, pair_index, project_index):
        result = process_qa_pair_task.apply_async(
            args=[
                str(self.pairs[pair_index]),
                str(self.projects[project_index]),
            ],
            queue=self.queue,
            exchange=self.queue,
            routing_key=self.queue,
        )
        self.results.append(result)
        return result.get(timeout=60, disable_sync_subtasks=False)

    async def snapshot(self):
        async with self.session() as db:
            pairs = (await db.execute(select(QAPair))).scalars().all()
            docs = (await db.execute(select(FileDocument))).scalars().all()
            return {
                "pairs": {p.id: (p.status, p.document_id) for p in pairs},
                "docs": {
                    d.id: (
                        d.project_id,
                        d.collection_id,
                        d.content,
                        tuple(d.embedding),
                    )
                    for d in docs
                },
            }

    async def assert_vectors(self):
        async with self.session() as db:
            for index, project in enumerate(self.projects):
                document = (
                    await db.execute(
                        select(FileDocument).where(
                            FileDocument.project_id == project,
                        )
                    )
                ).scalar_one()
                assert document.collection_id == self.collections[index]
                assert document.tags["qa_pair_id"] == str(self.pairs[index])
                assert document.embedding_dimensions == 1536
                assert document.embedding[index] == 1
                assert document.embedding[1 - index] == 0
                assert document.content_tsv
                assert f"Synthetic private answer {index}" in document.content
            assert (
                await db.scalar(select(func.count()).select_from(FileDocument))
                == 2
            )

    async def remove_collection(self):
        async with self.session() as db:
            collection = await db.get(Collection, self.collections[0])
            collection.deleted_at = datetime.now(timezone.utc)
            await db.commit()

    async def cleanup_database(self):
        assert re.fullmatch(r"qa_worker_verify_[0-9a-f]{32}", self.schema)
        engine = self.engine()
        try:
            async with engine.begin() as connection:
                owner = await connection.scalar(
                    text(
                        "SELECT obj_description(oid, 'pg_namespace') "
                        "FROM pg_namespace WHERE nspname=:schema"
                    ),
                    {"schema": self.schema},
                )
                if owner is not None:
                    assert owner == self.marker, "Fixture ownership differs"
                    await connection.execute(
                        text(f'DROP SCHEMA "{self.schema}" CASCADE')
                    )
                exists = await connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_namespace "
                        "WHERE nspname=:schema"
                    ),
                    {"schema": self.schema},
                )
                assert exists == 0
        finally:
            await engine.dispose()

    def run_checks(self):
        before = asyncio.run(self.snapshot())
        denied = self.submit(0, 1)
        assert denied["success"] is False
        assert self.embedded_projects == []
        assert asyncio.run(self.snapshot()) == before
        print(
            "PASS: queued cross-company job denied before embedding",
            flush=True,
        )
        for index in range(2):
            processed = self.submit(index, index)
            assert processed["success"], processed.get("error")
        asyncio.run(self.assert_vectors())
        before = asyncio.run(self.snapshot())
        replayed = self.submit(0, 0)
        assert replayed["success"]
        assert asyncio.run(self.snapshot()) == before
        print(
            "PASS: both company vectors persisted; replay did not duplicate "
            "or overwrite other company",
            flush=True,
        )
        asyncio.run(self.remove_collection())
        embedding_count = len(self.embedded_projects)
        refused = self.submit(0, 0)
        assert refused["success"] is False
        assert len(self.embedded_projects) == embedding_count
        assert asyncio.run(self.snapshot()) == before
        print(
            "PASS: deleted collection blocks later queued processing",
            flush=True,
        )

    def run(self):
        broker = redis.Redis.from_url(self.settings.celery_broker_url)
        assert broker.ping()
        broker.close()
        original_session = qa_documents.get_db_session
        original_embedding = qa_documents.get_embedding_service_for_project
        worker_context = None
        worker_stopped = True
        try:
            asyncio.run(self.seed())
            qa_documents.get_db_session = self.session
            qa_documents.get_embedding_service_for_project = (
                self.embedding_service
            )
            exchange = Exchange(self.queue, type="direct")
            celery_app.conf.update(
                task_always_eager=False,
                task_queues=(
                    Queue(self.queue, exchange, routing_key=self.queue),
                ),
                task_routes={
                    "process_qa_pair_task": {
                        "queue": self.queue,
                        "exchange": self.queue,
                        "routing_key": self.queue,
                    }
                },
                task_create_missing_queues=False,
                task_default_queue=self.queue,
                task_default_exchange=self.queue,
                task_default_routing_key=self.queue,
                worker_enable_remote_control=False,
                task_send_sent_event=False,
                worker_send_task_events=False,
            )
            worker_context = start_worker(
                celery_app,
                pool="solo",
                queues=[self.queue],
                perform_ping_check=False,
                shutdown_timeout=30,
                loglevel="CRITICAL",
            )
            worker_stopped = False
            worker_context.__enter__()
            self.run_checks()
        finally:
            try:
                if worker_context is not None:
                    worker_context.__exit__(None, None, None)
                    worker_stopped = True
            finally:
                qa_documents.get_db_session = original_session
                qa_documents.get_embedding_service_for_project = (
                    original_embedding
                )
                if worker_stopped:
                    for result in self.results:
                        result.forget()
                    with celery_app.connection_for_write() as connection:
                        channel = connection.channel()
                        channel.queue_delete(queue=self.queue)
                        channel.exchange_delete(exchange=self.queue)
                    asyncio.run(self.cleanup_database())
                    print(
                        "PASS: test worker stopped; only owned queue, results "
                        "and schema removed",
                        flush=True,
                    )
                else:
                    print(
                        "Retained private fixture for inspection: "
                        f"{self.schema}",
                        flush=True,
                    )


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    Verification().run()
