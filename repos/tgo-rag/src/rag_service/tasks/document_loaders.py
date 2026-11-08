"""Read one supplied file using the existing PDF, text, HTML or Word parser."""

from pathlib import Path
from typing import Iterator, List

from langchain_community.document_loaders.base import BaseBlobParser
from langchain_community.document_loaders.blob_loaders import BlobLoader
from langchain_community.document_loaders.blob_loaders.schema import Blob
from langchain_community.document_loaders.generic import GenericLoader
from langchain_community.document_loaders.parsers import (
    BS4HTMLParser,
    PDFMinerParser,
)
from langchain_community.document_loaders.parsers.txt import TextParser

from ..logging_config import get_logger
from ..services.qa_errors import safe_exception_chain
from .document_processing_errors import DocumentProcessingError, ProcessingStep
from .document_processing_types import (
    SUPPORTED_CONTENT_TYPES,
    DocumentLoader as DocumentLoaderProtocol,
    ParserInfo,
)

logger = get_logger(__name__)


class _SingleFileBlobLoader(BlobLoader):
    """Keep a file path literal, including after a delayed load starts."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def yield_blobs(self) -> Iterator[Blob]:
        if not self.path.is_file():
            raise ValueError("Document source must be an existing file.")
        yield Blob.from_path(self.path)


def get_document_loader(
    file_path: str, content_type: str, file_id: str
) -> DocumentLoaderProtocol:
    """
    Create a loader for one existing local file, never a directory or URL.
    """
    try:
        source = Path(file_path).expanduser()
        if not source.is_file():
            raise ValueError("Document source must be an existing file.")

        if content_type in (
            "application/msword",
            (
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
        ):
            return _get_word_document_loader(
                str(source), content_type, file_id
            )

        parser = _get_parser_for_content_type(content_type, file_id)
        loader = GenericLoader(_SingleFileBlobLoader(source), parser)
        logger.debug("Created document loader", file_id=file_id)
        return loader
    except DocumentProcessingError:
        raise
    except Exception as error:
        raise DocumentProcessingError(
            "Unable to create a document loader.",
            file_id,
            ProcessingStep.EXTRACTING_CONTENT,
            error,
        ) from error


def _get_word_document_loader(
    file_path: str, content_type: str, file_id: str
) -> DocumentLoaderProtocol:
    """Keep Unstructured first, with the existing DOCX-only fallback."""
    try:
        from langchain_community.document_loaders import (
            UnstructuredWordDocumentLoader,
        )

        loader = UnstructuredWordDocumentLoader(file_path)
        logger.debug("Created Word document loader", file_id=file_id)
        return loader
    except Exception as error:
        logger.warning(
            "Word document loader unavailable",
            file_id=file_id,
            loader="UnstructuredWordDocumentLoader",
            error_type=safe_exception_chain(error),
        )

    if (
        content_type == "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    ):
        try:
            from langchain_community.document_loaders import Docx2txtLoader

            fallback = Docx2txtLoader(file_path)
            logger.debug("Created DOCX fallback loader", file_id=file_id)
            return fallback
        except Exception as error:
            logger.warning(
                "DOCX fallback loader unavailable",
                file_id=file_id,
                loader="Docx2txtLoader",
                error_type=safe_exception_chain(error),
            )

    raise DocumentProcessingError(
        "No suitable Word document loader is available.",
        file_id,
        ProcessingStep.EXTRACTING_CONTENT,
    )


def _get_parser_for_content_type(
    content_type: str, file_id: str
) -> BaseBlobParser:
    """
    Select the existing parser without logging source or exception values.
    """
    try:
        if content_type == "application/pdf":
            logger.debug("Selected PDF parser", file_id=file_id)
            return PDFMinerParser()
        if content_type in ("text/plain", "text/markdown"):
            logger.debug("Selected text parser", file_id=file_id)
            return TextParser()
        if content_type in ("text/html", "application/xhtml+xml"):
            logger.debug("Selected HTML parser", file_id=file_id)
            return BS4HTMLParser()

        # The previous MIME parser call always failed without required
        # handlers,
        # so unknown types already used TextParser. Keep that effective policy.
        logger.debug("Selected text fallback parser", file_id=file_id)
        return TextParser()
    except Exception as error:
        raise DocumentProcessingError(
            "Unable to create a document parser.",
            file_id,
            ProcessingStep.EXTRACTING_CONTENT,
            error,
        ) from error


def get_supported_content_types() -> List[str]:
    """Return the existing supported MIME types."""
    return SUPPORTED_CONTENT_TYPES


def is_content_type_supported(content_type: str) -> bool:
    """Report explicit support; this does not expand upload admission rules."""
    return content_type in get_supported_content_types()


def get_parser_info(content_type: str) -> ParserInfo:
    """
    Describe the parser or primary loader actually selected for this type.
    """
    if content_type == "application/pdf":
        return ParserInfo(
            parser="PDFMinerParser",
            description=(
                "Specialized PDF text extraction with reliable formatting"
            ),
        )
    if content_type in ("text/plain", "text/markdown"):
        return ParserInfo(
            parser="TextParser",
            description="Plain text parser with UTF-8 encoding support",
        )
    if content_type in (
        "application/msword",
        (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    ):
        return ParserInfo(
            parser="UnstructuredWordDocumentLoader",
            description="Microsoft Word loader with a DOCX-only text fallback",
        )
    if content_type in ("text/html", "application/xhtml+xml"):
        return ParserInfo(
            parser="BS4HTMLParser",
            description="HTML parser using BeautifulSoup for text extraction",
        )
    return ParserInfo(
        parser="TextParser",
        description="Plain text fallback for unsupported types",
    )
