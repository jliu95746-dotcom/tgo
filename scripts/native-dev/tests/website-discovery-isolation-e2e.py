"""Real child crawl tasks plus concurrent SQL page reservation boundaries."""

import asyncio
import importlib.util
import logging
import time
from pathlib import Path

from sqlalchemy import select

spec = importlib.util.spec_from_file_location(
    "website_pipeline",
    Path(__file__).with_name("website-pipeline-isolation-e2e.py"),
)
website = importlib.util.module_from_spec(spec)
spec.loader.exec_module(website)
base = website.base

from src.rag_service.services.website_discovery import (  # noqa: E402
    reserve_child_pages,
)


class DiscoveryVerification(website.WebsitePipelineVerification):
    def __init__(self):
        super().__init__()
        self.auto_discover = True

    def page_body(self, path):
        parts = path.strip("/").split("/")
        if parts[0] not in ("company-0", "company-1"):
            return None
        company = parts[0]
        body = super().page_body("/" + company)
        if len(parts) == 1:
            targets = ["first", "first", "second", "third"]
        else:
            targets = ["grandchild"]
        links = "".join(
            f'<a href="/{company}/{target}">{target} product</a>'
            for target in targets
        )
        return body.replace("</body>", links + "</body>")

    async def seed(self):
        await super().seed()
        async with self.session() as db:
            for collection_id in self.collections:
                collection = await db.get(base.Collection, collection_id)
                collection.crawl_config = {
                    **collection.crawl_config,
                    "max_depth": 1,
                    "max_pages": 3,
                }
            await db.commit()

    async def wait_for_all_pages(self):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            async with self.session() as db:
                pages = (
                    (await db.execute(select(website.WebsitePage)))
                    .scalars()
                    .all()
                )
                assert not any(p.status == "failed" for p in pages), [
                    (p.status, p.error_message) for p in pages
                ]
                if len(pages) == 6 and all(
                    p.status == "processed" for p in pages
                ):
                    return
            await asyncio.sleep(0.2)
        raise AssertionError("Child page indexing did not finish")

    async def check_persistence_and_search(self):
        await self.wait_for_all_pages()
        service = base.search.SearchService()
        service.vector_store_service = self.vector_service
        for index, project_id in enumerate(self.projects):
            async with self.session() as db:
                pages = (
                    (
                        await db.execute(
                            select(website.WebsitePage).where(
                                website.WebsitePage.project_id == project_id,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(pages) == 3
                children = [p for p in pages if p.depth == 1]
                assert len(children) == 2
                assert all(
                    p.parent_page_id == self.page_ids[index]
                    and p.collection_id == self.collections[index]
                    and p.crawl_config["max_depth"] == 1
                    for p in children
                )
                assert {p.url.rsplit("/", 1)[-1] for p in children} == {
                    "first",
                    "second",
                }
                page_files = {p.file_id for p in pages}
                files = (
                    (
                        await db.execute(
                            select(base.File).where(
                                base.File.id.in_(page_files)
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(files) == 3
                assert all(
                    f.status == "completed"
                    and f.project_id == project_id
                    and f.collection_id == self.collections[index]
                    for f in files
                )
                documents = (
                    (
                        await db.execute(
                            select(base.FileDocument).where(
                                base.FileDocument.project_id == project_id,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(documents) == sum(f.document_count for f in files)
                assert {d.file_id for d in documents} == page_files
                assert all(
                    d.collection_id == self.collections[index]
                    and len(d.embedding) == 1536
                    for d in documents
                )
                expected_ids = {d.id for d in documents}
            results = await service.semantic_search(
                query=f"company-{1-index}",
                project_id=project_id,
                collection_id=self.collections[index],
                limit=100,
            )
            assert {r.document_id for r in results.results} == expected_ids
            assert all(
                f"company-{index}" in r.content_preview
                for r in results.results
            )
            foreign = await service.semantic_search(
                query="Private",
                project_id=project_id,
                collection_id=self.collections[1 - index],
                limit=100,
            )
            assert foreign.results == []
        assert all(
            not p.endswith(("/third", "/grandchild")) for p in self.requests
        )
        print(
            "PASS: six real page tasks and indexes preserve tenant, "
            "parent, depth, deduplication and page limit",
            flush=True,
        )

    async def check_concurrent_reservations(self):
        # Company-0 was intentionally corrupted by the inherited negative test.
        # Use company-1's valid root and grant exactly one remaining page here.
        index = 1
        async with self.session() as db:
            collection = await db.get(base.Collection, self.collections[index])
            collection.crawl_config = {
                **collection.crawl_config,
                "max_pages": 4,
            }
            await db.commit()
        ready = asyncio.Event()
        entered = 0

        async def reserve(label):
            nonlocal entered
            async with self.session() as db:
                # Separate physical connections and transactions.
                entered += 1
                if entered == 2:
                    ready.set()
                await asyncio.wait_for(ready.wait(), 10)
                url = (
                    f"http://127.0.0.1:{self.server.server_port}"
                    f"/company-1/race-{label}"
                )
                result = await reserve_child_pages(
                    db,
                    self.page_ids[index],
                    self.projects[index],
                    [url, url],
                    max_depth=1,
                )
                await db.commit()
                return result

        first, second = await asyncio.wait_for(
            asyncio.gather(reserve("a"), reserve("b")),
            20,
        )
        assert len(first.page_ids) + len(second.page_ids) == 1
        assert first.limit_reached != second.limit_reached
        async with self.session() as db:
            rows = (
                (await db.execute(select(website.WebsitePage))).scalars().all()
            )
            assert len(rows) == 7
            assert sum(p.project_id == self.projects[1] for p in rows) == 5
            denied = await reserve_child_pages(
                db,
                self.page_ids[1],
                self.projects[0],
                ["http://127.0.0.1/forbidden"],
                max_depth=1,
            )
            assert denied.page_ids == []
            await db.rollback()
        print(
            "PASS: concurrent SQL reservations cannot exceed one remaining "
            "page; foreign company reservation denied",
            flush=True,
        )

    def run_checks(self):
        super().run_checks()
        asyncio.run(self.check_concurrent_reservations())


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    DiscoveryVerification().run()
