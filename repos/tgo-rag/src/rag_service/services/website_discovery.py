"""Reserve child URLs under a collection lock shared by every creation path."""

import hashlib
from dataclasses import dataclass, field
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Collection, WebsitePage

CrawlOverrides = dict[str, JsonValue]


@dataclass
class ReservedPages:
    page_ids: list[UUID] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    skipped: int = 0
    limit_reached: bool = False


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def select_new_urls(
    urls: list[str],
    existing_hashes: set[str],
    remaining: int,
) -> list[str]:
    return [url for url in dict.fromkeys(urls) if url_hash(url) not in existing_hashes][
        : max(0, remaining)
    ]


async def reserve_child_pages(
    db: AsyncSession,
    page_id: UUID,
    project_id: UUID,
    urls: list[str],
    max_depth: int,
    overrides: CrawlOverrides | None = None,
    crawl_source: str = "discovered",
) -> ReservedPages:
    result = await db.execute(
        select(WebsitePage, Collection)
        .join(Collection, Collection.id == WebsitePage.collection_id)
        .where(
            WebsitePage.id == page_id,
            WebsitePage.project_id == project_id,
            Collection.project_id == project_id,
            Collection.deleted_at.is_(None),
        )
        .with_for_update(of=(Collection, WebsitePage))
        .execution_options(populate_existing=True)
    )
    row = result.one_or_none()
    if row is None:
        return ReservedPages()
    source, collection = row
    if source.depth >= max_depth:
        return ReservedPages(skipped=len(set(urls)))
    page_count = await db.scalar(
        select(func.count())
        .select_from(WebsitePage)
        .where(
            WebsitePage.collection_id == source.collection_id,
            WebsitePage.project_id == project_id,
        )
    )
    remaining = max(
        0,
        int((collection.crawl_config or {}).get("max_pages", 100)) - (page_count or 0),
    )
    unique_urls = list(dict.fromkeys(urls))
    hashes = [url_hash(url) for url in unique_urls]
    existing_result = await db.execute(
        select(WebsitePage.url_hash).where(
            WebsitePage.collection_id == source.collection_id,
            WebsitePage.project_id == project_id,
            WebsitePage.url_hash.in_(hashes),
        )
    )
    existing_hashes = set(existing_result.scalars().all())
    new_urls = select_new_urls(unique_urls, existing_hashes, remaining)
    total_new = sum(url_hash(url) not in existing_hashes for url in unique_urls)
    reserved = ReservedPages(
        urls=new_urls,
        skipped=len(unique_urls) - len(new_urls),
        limit_reached=total_new > remaining,
    )
    config: CrawlOverrides = {
        **(source.crawl_config or {}),
        **(overrides or {}),
        "max_depth": max_depth,
    }
    for url in new_urls:
        child = WebsitePage(
            collection_id=source.collection_id,
            project_id=project_id,
            parent_page_id=page_id,
            url=url,
            url_hash=url_hash(url),
            depth=source.depth + 1,
            status="pending",
            crawl_source=crawl_source,
            content_length=0,
            crawl_config=dict(config),
        )
        db.add(child)
        await db.flush()
        reserved.page_ids.append(child.id)

    created_hashes = existing_hashes | {url_hash(url) for url in new_urls}
    # SQLAlchemy JSONB does not track in-place edits to nested lists/dicts.
    if isinstance(source.discovered_links, list):
        source.discovered_links = [
            {
                **link,
                "created": bool(link.get("created"))
                or url_hash(str(link.get("url", ""))) in created_hashes,
            }
            for link in source.discovered_links
            if isinstance(link, dict)
        ]
    return reserved
