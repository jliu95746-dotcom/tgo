"""Real HTTP crawl, queue, website publication and tenant search checks."""

import asyncio
import importlib.util
import logging
import tempfile
import threading
import time
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from celery.signals import after_task_publish
from sqlalchemy import delete, select

spec = importlib.util.spec_from_file_location(
    "file_pipeline",
    Path(__file__).with_name("file-pipeline-isolation-e2e.py"),
)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

from src.rag_service.models import WebsitePage  # noqa: E402
from src.rag_service.services import (  # noqa: E402
    crawler,
    website_dispatch,
    website_documents,
)
from src.rag_service.tasks import website_crawling  # noqa: E402


class WebsitePipelineVerification(base.FilePipelineVerification):
    def __init__(self):
        super().__init__()
        self.page_ids = [uuid4(), uuid4()]
        self.requests = []
        self.published = set()
        self.server = None
        self.auto_discover = False

    def page_body(self, path):
        if path not in ("/company-0", "/company-1"):
            return None
        company = path.removeprefix("/")
        return (
            f"<html><head><title>{company}</title></head><body>"
            + (
                f"<p>Private {company} record-{self.marker} "
                "product documentation and support details.</p>"
            )
            * 90
            + "</body></html>"
        )

    def serve(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append(self.path)
                if self.path == "/robots.txt":
                    body = "User-agent: *\nAllow: /\n"
                else:
                    body = owner.page_body(self.path)
                if body is None:
                    self.send_error(404)
                    return
                payload = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server_thread = threading.Thread(target=self.server.serve_forever)
        self.server_thread.start()

    async def seed(self):
        await super().seed()
        engine = self.engine()
        try:
            async with engine.begin() as connection:
                await connection.run_sync(WebsitePage.__table__.create)
            async with self.session() as db:
                await db.execute(delete(base.File))
                for index in range(2):
                    collection = await db.get(
                        base.Collection, self.collections[index]
                    )
                    collection.collection_type = base.CollectionType.website
                    collection.crawl_config = {
                        "render_js": False,
                        "delay_seconds": 0,
                        "max_depth": 0,
                        "respect_robots_txt": True,
                    }
                    url = (
                        f"http://127.0.0.1:{self.server.server_port}"
                        f"/company-{index}"
                    )
                    db.add(
                        WebsitePage(
                            id=self.page_ids[index],
                            project_id=self.projects[index],
                            collection_id=self.collections[index],
                            url=url,
                            url_hash=crawler.url_hash(url),
                            status="pending",
                            depth=0,
                        )
                    )
                await db.commit()
        finally:
            await engine.dispose()

    async def embedding_service(self, project_id):
        index = [str(p) for p in self.projects].index(str(project_id))
        client = base.TestEmbeddings(index)

        async def generate(texts):
            return client.embed_documents(texts)

        return SimpleNamespace(
            embeddings_client=client,
            generate_embeddings_batch=generate,
            get_embedding_model=lambda: "isolated-test-vector",
        )

    def track_publish(self, sender=None, headers=None, **kwargs):
        if sender in ("crawl_page_task", "process_file_task"):
            self.published.add(headers["id"])

    def submit_page(self, index):
        result = website_crawling.crawl_page_task.apply_async(
            args=[str(self.page_ids[index]), self.auto_discover],
            queue=self.queue,
            exchange=self.queue,
            routing_key=self.queue,
        )
        self.results.append(result)
        return result.get(timeout=60, disable_sync_subtasks=False)

    async def wait_for_page(self, index):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            async with self.session() as db:
                page = await db.get(WebsitePage, self.page_ids[index])
                assert page.status != "failed", page.error_message
                if page.status == "processed":
                    self.file_ids[index] = page.file_id
                    assert page.project_id == self.projects[index]
                    assert page.collection_id == self.collections[index]
                    assert f"company-{index}" in page.content_markdown
                    return
            await asyncio.sleep(0.2)
        raise AssertionError("Website processing did not finish in 45 seconds")

    async def snapshot_documents(self):
        async with self.session() as db:
            rows = (await db.execute(select(base.FileDocument))).scalars()
            return sorted(
                (str(row.id), str(row.project_id), row.content) for row in rows
            )

    async def corrupt_page_owner(self):
        async with self.session() as db:
            page = await db.get(WebsitePage, self.page_ids[0])
            page.project_id = self.projects[1]
            page.status = "pending"
            await db.commit()

    def run_checks(self):
        try:
            for index in range(2):
                response = self.submit_page(index)
                assert response["status"] == "success", response
                asyncio.run(self.wait_for_page(index))
                assert f"/company-{index}" in self.requests
                print(
                    f"PASS: company-{index} real HTTP crawl and queued "
                    "website indexing completed",
                    flush=True,
                )
            asyncio.run(self.check_persistence_and_search())
            before = asyncio.run(self.snapshot_documents())
            request_count = len(self.requests)
            replay = self.submit_page(0)
            assert replay["status"] == "skipped", replay
            asyncio.run(self.corrupt_page_owner())
            refused = self.submit_page(0)
            assert refused["status"] == "failed", refused
            assert refused["error"] == "Page not found", refused
            assert len(self.requests) == request_count
            assert asyncio.run(self.snapshot_documents()) == before
            print(
                "PASS: replay and mismatched page owner caused no fetch "
                "or document changes",
                flush=True,
            )
        finally:
            # Include automatically dispatched document-task results.
            for task_id in self.published:
                self.results.append(
                    website_crawling.celery_app.AsyncResult(task_id)
                )
            if self.directory.exists():
                for path in self.directory.iterdir():
                    if path not in self.paths:
                        UUID(path.name)
                        assert path.parent.resolve() == self.directory
                        self.paths.append(path)

    def run(self):
        self.serve()
        after_task_publish.connect(self.track_publish)
        try:
            with ExitStack() as replacements:
                cache = replacements.enter_context(
                    tempfile.TemporaryDirectory(
                        prefix="website-crawl-",
                        dir=base.ROOT / ".tmp",
                    )
                )
                original_crawler = crawler.AsyncWebCrawler
                replacements.enter_context(
                    patch.object(
                        crawler,
                        "AsyncWebCrawler",
                        lambda **kwargs: original_crawler(
                            base_directory=cache, **kwargs
                        ),
                    )
                )
                for module in (
                    website_crawling,
                    website_dispatch,
                    website_documents,
                ):
                    replacements.enter_context(
                        patch.object(
                            module,
                            "get_db_session",
                            self.session,
                        )
                    )
                replacements.enter_context(
                    patch.object(
                        website_documents,
                        "get_embedding_service_for_project",
                        self.embedding_service,
                    )
                )
                replacements.enter_context(
                    patch.object(
                        website_crawling.settings,
                        "upload_dir",
                        str(self.directory),
                    )
                )
                super().run()
        finally:
            after_task_publish.disconnect(self.track_publish)
            self.server.shutdown()
            self.server.server_close()
            self.server_thread.join(timeout=5)
            assert not self.server_thread.is_alive()
            print(
                "PASS: owned HTTP server and crawler cache removed", flush=True
            )


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    WebsitePipelineVerification().run()
