"""Prepare revisions without writing any searchable document."""
import asyncio
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from langchain_core.documents import Document
from sqlalchemy import select

from ..config import get_settings
from ..database import get_db_session
from ..models import Collection, File, WebsitePage
from ..models.knowledge_versions import KnowledgeVersion
from ..logging_config import get_logger
from ..schemas.knowledge_versions import VersionChunk, VersionSnapshot
from .embedding import get_embedding_service_for_project
from .knowledge_version_publish import locked_version, publish
from .knowledge_versions import VersionConflict, snapshot_digest, validate_snapshot
from .qa_documents import build_qa_content
from .qa_errors import QA_UNKNOWN_FAILURE, safe_exception_chain, safe_qa_failure
from .crawl_errors import CrawlError

logger = get_logger(__name__)


def safe_version_failure(error: Exception) -> str:
    if isinstance(error, (VersionConflict, CrawlError)):
        return str(error)
    chain = safe_exception_chain(error)
    if any(name in chain for name in ['APIConnectionError', 'RemoteProtocolError', 'ConnectError', 'Timeout']):
        return f'模型服务连接失败（{chain}），请重试。原生效内容未改变。'
    reason = safe_qa_failure(error)
    if reason == QA_UNKNOWN_FAILURE:
        return f'新版处理失败（{chain}），请联系管理员检查处理日志。原生效内容未改变。'
    return str(reason)


async def prepare_snapshot(project: UUID, version_id: UUID, attempt: UUID) -> VersionSnapshot:
    from .company_resources import require_processing
    await require_processing(project)
    from ..tasks.document_chunking import chunk_documents
    from ..tasks.document_loaders import get_document_loader
    from ..tasks.website_crawling import CrawlConfig, merge_crawl_configs
    from .crawler import CrawlOptions, WebCrawlerService

    async with get_db_session() as db:
        version, source, parent = await locked_version(db, project, version_id)
        snapshot: VersionSnapshot = VersionSnapshot.model_validate(version.snapshot)
        if version.state != 'processing' or snapshot.attempt != attempt:
            raise VersionConflict('这次处理已失效。')
        collection_id, source_id, kind = source.collection_id, source.id, source.source_kind
        if kind == 'file':
            file = await db.get(File, snapshot.content.replacement_file_id)
            if (not file or file.project_id != project or file.deleted_at
                    or file.collection_id is not None
                    or (file.storage_metadata or {}).get('version_source') != str(source_id)):
                raise VersionConflict('替换文件已不可用。')
            snapshot.filename, snapshot.storage_path = file.original_filename, file.storage_path
            snapshot.file_size, snapshot.content_type = file.file_size, file.content_type
        if isinstance(parent, WebsitePage):
            collection = await db.get(Collection, collection_id)
            config = CrawlConfig.from_dict(merge_crawl_configs(collection.crawl_config, parent.crawl_config))
            url, depth = parent.url, parent.depth
        await db.commit()

    if kind == 'qa':
        if not snapshot.content.question or not snapshot.content.answer:
            raise VersionConflict('问题和答案不能为空。')
        contents = [build_qa_content(snapshot.content.question, snapshot.content.answer)]
    else:
        if kind == 'website':
            headers = config.headers
            if config.headers_origin:
                origin, target = urlparse(config.headers_origin), urlparse(url)
                if (origin.scheme, origin.netloc.lower()) != (target.scheme, target.netloc.lower()):
                    headers = None
            crawler = WebCrawlerService(options=CrawlOptions(
                render_js=config.render_js, respect_robots_txt=config.respect_robots_txt,
                delay_seconds=config.delay_seconds, user_agent=config.user_agent,
                timeout_seconds=config.timeout_seconds, headers=headers, wait_time=config.wait_time,
                follow_external_links=config.follow_external_links,
            ), include_patterns=config.include_patterns, exclude_patterns=config.exclude_patterns)
            page = await crawler.crawl_page(url, depth=depth)
            if not page or not page.content_markdown.strip():
                raise VersionConflict('网站没有返回可用正文，原内容未改变。')
            documents = [Document(page_content=page.content_markdown)]
            path = Path(get_settings().upload_dir) / f'version-{attempt}.md'
            snapshot.website_markdown, snapshot.website_title = page.content_markdown, page.title
            snapshot.filename, snapshot.storage_path = (page.title or '网站资料') + '.md', str(path)
            snapshot.file_size, snapshot.content_type = len(page.content_markdown.encode()), 'text/markdown'
        else:
            loader = get_document_loader(snapshot.storage_path, snapshot.content_type, str(source_id))
            documents = await asyncio.to_thread(loader.load)
        chunks = await asyncio.to_thread(chunk_documents, documents, str(source_id), source_id, collection_id, project)
        contents = [str(chunk['content']) for chunk in chunks]
    # Digest comparison precedes paid embedding calls. Embeddings are deliberately not part of the digest.
    snapshot.chunks = [VersionChunk(content=text, embedding=[], embedding_model='',
                                   content_type='qa_pair' if kind == 'qa' else 'paragraph') for text in contents]
    async with get_db_session() as db:
        version, source, _ = await locked_version(db, project, version_id)
        active = (await db.execute(select(KnowledgeVersion).where(
            KnowledgeVersion.source_id == source.id, KnowledgeVersion.number == source.active_number,
        ))).scalar_one_or_none()
        unchanged = active is not None and active.digest == snapshot_digest(snapshot)
    if not unchanged:
        if kind == 'website':
            assert snapshot.storage_path is not None and snapshot.website_markdown is not None
            await asyncio.to_thread(Path(snapshot.storage_path).write_text, snapshot.website_markdown, encoding='utf-8')
        service = await get_embedding_service_for_project(project)
        vectors = await service.generate_embeddings_batch(contents)
        if len(vectors) != len(contents):
            raise VersionConflict('新版处理结果不完整，原内容未改变。')
        for chunk, vector in zip(snapshot.chunks, vectors):
            chunk.embedding, chunk.embedding_model = vector, service.get_embedding_model()
            chunk.token_count = len(chunk.content.split())
        validate_snapshot(snapshot)
    return snapshot


async def process_revision(project: UUID, version_id: UUID, attempt: UUID) -> str:
    try:
        snapshot = await prepare_snapshot(project, version_id, attempt)
        async with get_db_session() as db:
            version, source, _ = await locked_version(db, project, version_id)
            if version.state != 'processing' or VersionSnapshot.model_validate(version.snapshot).attempt != attempt:
                return 'skipped'
            digest = snapshot_digest(snapshot)
            active = (await db.execute(select(KnowledgeVersion).where(
                KnowledgeVersion.source_id == source.id, KnowledgeVersion.number == source.active_number,
            ))).scalar_one_or_none()
            if active and active.digest == digest:
                version.state, version.error = 'unchanged', None
                # Keep a lightweight check result, reuse this number on the next actual update.
                version.snapshot = VersionSnapshot(content=snapshot.content, attempt=attempt).model_dump(mode='json')
            else:
                validate_snapshot(snapshot)
                version.snapshot, version.digest = snapshot.model_dump(mode='json'), digest
                version.state = 'pending_review' if version.requested_action == 'submit' else 'ready'
                await db.flush()
                if version.requested_action == 'publish':
                    await publish(db, project, version_id, version.author)
            await db.commit()
            return str(version.state)
    except Exception as error:
        logger.error('Knowledge version preparation failed', version_id=str(version_id),
                     error_type=safe_exception_chain(error))
        async with get_db_session() as db:
            version = await db.get(KnowledgeVersion, version_id, with_for_update=True)
            if (version and version.state == 'processing'
                    and VersionSnapshot.model_validate(version.snapshot).attempt == attempt):
                version.state = 'failed'
                version.error = safe_version_failure(error)
                await db.commit()
        return 'failed'
