"""Coordinate page/file state at the crawler-to-document queue boundary."""

from ..database import get_db_session
from ..models import File
from .website_documents import current_source


async def prepare_website_dispatch(source: File) -> bool:
    """Only the current, extracted generation may enter the document queue."""
    async with get_db_session() as db:
        current = await current_source(db, source)
        if current is None:
            return False
        page, file = current
        if page.status != "extracted" or file.status != "pending":
            return False
        page.status = "processing"
        page.error_message = None
        await db.commit()
        return True


async def fail_website_dispatch(source: File, safe_error: str) -> bool:
    """Fail both records unless a consumer already claimed the file.

    A broker can accept a message even when its acknowledgement is lost. Taking
    the same locks as the document consumer resolves that race: either it has
    claimed the file and we leave it alone, or failure wins and a late consumer
    skips it. No searchable documents or older published files are changed.
    """
    async with get_db_session() as db:
        current = await current_source(db, source)
        if current is None:
            return False
        page, file = current
        if (
            page.status not in {"extracted", "processing"}
            or file.status != "pending"
        ):
            return False
        page.status = file.status = "failed"
        page.error_message = file.error_message = safe_error
        await db.commit()
        return True
