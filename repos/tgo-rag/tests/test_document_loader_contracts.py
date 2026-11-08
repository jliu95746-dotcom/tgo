"""Read actual fixture bytes without matching or opening unrelated files."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock
from zipfile import ZipFile

import langchain_community.document_loaders as sdk_loaders
import pytest
from langchain_community.document_loaders.word_document import Docx2txtLoader
from langchain_core.documents import Document

from src.rag_service.tasks import document_loaders as loaders


FILE_ID = "fixture-file-id"
DOCX = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
PRIVATE_FAILURE = "https://fixture:private-token@example.invalid/document"


def minimal_pdf(path: Path) -> None:
    stream = b"BT /F1 12 Tf 72 720 Td (Fixture knowledge text.) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, content in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(str(number).encode() + b" 0 obj\n" + content)
        output.extend(b"\nendobj\n")
    startxref = len(output)
    output.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n")
    output.extend(str(startxref).encode() + b"\n%%EOF\n")
    path.write_bytes(output)


@pytest.mark.parametrize(
    "name",
    ["说明.txt", "商品资料.md", "资料[01].txt", "规格[甲乙].md", "资料 #空格.txt"],
)
def test_only_the_exact_target_file_is_loaded(tmp_path, name):
    source = tmp_path / name
    source.write_text("指定商品资料\n保修一年。", encoding="utf-8")
    for decoy in ("资料0.txt", "资料1.txt", "规格甲.md", "another.txt"):
        (tmp_path / decoy).write_text("另一份私有资料", encoding="utf-8")
    result = loaders.get_document_loader(
        str(source), "text/plain", FILE_ID
    ).load()
    assert len(result) == 1
    assert result[0].page_content == "指定商品资料\n保修一年。"
    assert result[0].metadata["source"] == str(source)


@pytest.mark.parametrize(
    "content_type", ["text/plain", "text/markdown", "application/x-fixture"]
)
def test_native_text_and_existing_unknown_type_fallback(
    tmp_path, content_type
):
    source = tmp_path / "notes.unknown"
    source.write_text("# 商品资料\n支持七天退货。", encoding="utf-8")
    result = loaders.get_document_loader(
        str(source), content_type, FILE_ID
    ).load()
    assert [item.page_content for item in result] == ["# 商品资料\n支持七天退货。"]


@pytest.mark.parametrize(
    "content_type", ["text/html", "application/xhtml+xml"]
)
def test_native_html_extracts_readable_text(tmp_path, content_type):
    source = tmp_path / "policy.html"
    source.write_text(
        "<html><head><title>资料</title></head>"
        "<body><h1>售后政策</h1><p>七天内申请退货。</p></body></html>",
        encoding="utf-8",
    )
    result = loaders.get_document_loader(
        str(source), content_type, FILE_ID
    ).load()
    assert len(result) == 1
    assert "售后政策" in result[0].page_content
    assert "七天内申请退货。" in result[0].page_content
    assert "<h1>" not in result[0].page_content


def test_native_pdf_extracts_real_document_bytes(tmp_path):
    source = tmp_path / "knowledge.pdf"
    minimal_pdf(source)
    result = loaders.get_document_loader(
        str(source), "application/pdf", FILE_ID
    ).load()
    assert len(result) == 1
    assert "Fixture knowledge text." in result[0].page_content


def test_native_docx_fallback_reads_real_openxml(tmp_path, monkeypatch):
    source = tmp_path / "knowledge.docx"
    with ZipFile(source, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"><w:body><w:p><w:r>'
            "<w:t>商品保修一年。</w:t></w:r></w:p></w:body></w:document>",
        )

    def unavailable(_path):
        raise ImportError("fixture optional parser unavailable")

    monkeypatch.setattr(
        sdk_loaders, "UnstructuredWordDocumentLoader", unavailable
    )
    monkeypatch.setattr(sdk_loaders, "Docx2txtLoader", Docx2txtLoader)
    result = loaders.get_document_loader(str(source), DOCX, FILE_ID).load()
    assert [item.page_content for item in result] == ["商品保修一年。"]


def test_word_primary_loader_keeps_priority(tmp_path, monkeypatch):
    source = tmp_path / "knowledge.docx"
    source.write_bytes(b"controlled constructor fixture")
    primary = MagicMock()
    primary.load.return_value = [Document(page_content="主要读取器结果")]
    first = MagicMock(return_value=primary)
    fallback = MagicMock()
    monkeypatch.setattr(sdk_loaders, "UnstructuredWordDocumentLoader", first)
    monkeypatch.setattr(sdk_loaders, "Docx2txtLoader", fallback)
    assert loaders.get_document_loader(str(source), DOCX, FILE_ID) is primary
    first.assert_called_once_with(str(source))
    fallback.assert_not_called()


def test_legacy_doc_does_not_use_docx_fallback(tmp_path, monkeypatch):
    source = tmp_path / "legacy.doc"
    source.write_bytes(b"controlled legacy document fixture")
    unavailable = MagicMock(side_effect=ImportError("fixture unavailable"))
    fallback = MagicMock()
    monkeypatch.setattr(
        sdk_loaders, "UnstructuredWordDocumentLoader", unavailable
    )
    monkeypatch.setattr(sdk_loaders, "Docx2txtLoader", fallback)
    with pytest.raises(loaders.DocumentProcessingError):
        loaders.get_document_loader(str(source), "application/msword", FILE_ID)
    fallback.assert_not_called()


@pytest.mark.parametrize("kind", ["directory", "missing"])
def test_directory_and_missing_paths_are_not_accepted(tmp_path, kind):
    source = tmp_path / "document.txt"
    if kind == "directory":
        source.mkdir()
        (source / "private.txt").write_text("不应读取", encoding="utf-8")
    with pytest.raises(loaders.DocumentProcessingError) as error:
        loaders.get_document_loader(str(source), "text/plain", FILE_ID)
    assert error.value.file_id == FILE_ID
    assert error.value.step is loaders.ProcessingStep.EXTRACTING_CONTENT


@pytest.mark.parametrize("replacement", ["removed", "directory"])
def test_source_disappearing_after_creation_never_scans_a_directory(
    tmp_path, replacement
):
    source = tmp_path / "document.txt"
    source.write_text("原资料", encoding="utf-8")
    loader = loaders.get_document_loader(str(source), "text/plain", FILE_ID)
    source.unlink()
    if replacement == "directory":
        source.mkdir()
        (source / "private.txt").write_text("不应读取", encoding="utf-8")
    with pytest.raises((ValueError, FileNotFoundError, IsADirectoryError)):
        loader.load()


def test_relative_file_path_is_loaded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("relative.txt").write_text("相对路径资料", encoding="utf-8")
    result = loaders.get_document_loader(
        "relative.txt", "text/plain", FILE_ID
    ).load()
    assert [item.page_content for item in result] == ["相对路径资料"]


def test_unknown_type_does_not_construct_a_broken_mime_parser(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(loaders, "logger", logger)
    parser = loaders._get_parser_for_content_type(
        "application/x-fixture", FILE_ID
    )
    assert isinstance(parser, loaders.TextParser)
    logger.warning.assert_not_called()


def test_loader_failure_preserves_context_without_public_secrets(
    tmp_path, monkeypatch
):
    source = tmp_path / "document.txt"
    source.write_text("资料", encoding="utf-8")
    original = RuntimeError(PRIVATE_FAILURE)

    class BrokenLoader:
        def __init__(self, *args, **kwargs):
            raise original

        @classmethod
        def from_filesystem(cls, *args, **kwargs):
            raise original

    monkeypatch.setattr(loaders, "GenericLoader", BrokenLoader)
    with pytest.raises(loaders.DocumentProcessingError) as error:
        loaders.get_document_loader(str(source), "text/plain", FILE_ID)
    assert error.value.__cause__ is original
    assert error.value.original_exception is original
    assert error.value.file_id == FILE_ID
    assert error.value.step is loaders.ProcessingStep.EXTRACTING_CONTENT
    assert PRIVATE_FAILURE not in error.value.message


def test_parser_failure_is_not_rewrapped_or_exposed(tmp_path, monkeypatch):
    source = tmp_path / "document.pdf"
    source.write_bytes(b"controlled constructor failure")
    original = RuntimeError(PRIVATE_FAILURE)
    monkeypatch.setattr(
        loaders, "PDFMinerParser", MagicMock(side_effect=original)
    )
    with pytest.raises(loaders.DocumentProcessingError) as error:
        loaders.get_document_loader(str(source), "application/pdf", FILE_ID)
    assert error.value.__cause__ is original
    assert error.value.original_exception is original
    assert PRIVATE_FAILURE not in error.value.message


@pytest.mark.parametrize("error_type", [ImportError, RuntimeError])
def test_word_constructor_failures_are_safely_reported(
    tmp_path, monkeypatch, error_type
):
    source = tmp_path / "document.docx"
    source.write_bytes(b"controlled constructor failure")
    logger = MagicMock()
    monkeypatch.setattr(loaders, "logger", logger)
    for name in ("UnstructuredWordDocumentLoader", "Docx2txtLoader"):
        monkeypatch.setattr(
            sdk_loaders,
            name,
            MagicMock(side_effect=error_type(PRIVATE_FAILURE)),
        )
    with pytest.raises(loaders.DocumentProcessingError) as error:
        loaders.get_document_loader(str(source), DOCX, FILE_ID)
    assert PRIVATE_FAILURE not in error.value.message
    assert PRIVATE_FAILURE not in repr(logger.mock_calls)


def test_success_diagnostics_exclude_file_name_and_raw_content_type(
    tmp_path, monkeypatch
):
    source = tmp_path / "private-customer-attachment.txt"
    source.write_text("私有正文", encoding="utf-8")
    logger = MagicMock()
    monkeypatch.setattr(loaders, "logger", logger)
    content_type = "application/x-private-customer-format"
    loaders.get_document_loader(str(source), content_type, FILE_ID).load()
    output = repr(logger.mock_calls)
    for private in (source.name, str(tmp_path), content_type, "私有正文"):
        assert private not in output


@pytest.mark.parametrize(
    "signal", [KeyboardInterrupt, SystemExit, asyncio.CancelledError]
)
def test_shutdown_and_cancellation_propagate(tmp_path, monkeypatch, signal):
    source = tmp_path / "document.txt"
    source.write_text("资料", encoding="utf-8")
    original = signal()
    monkeypatch.setattr(
        loaders,
        "_get_parser_for_content_type",
        MagicMock(side_effect=original),
    )
    with pytest.raises(signal) as error:
        loaders.get_document_loader(str(source), "text/plain", FILE_ID)
    assert error.value is original


@pytest.mark.parametrize(
    "content_type,expected",
    [
        ("application/x-fixture", "TextParser"),
        (DOCX, "UnstructuredWordDocumentLoader"),
    ],
)
def test_parser_information_describes_the_actual_primary_parser(
    content_type, expected
):
    assert loaders.get_parser_info(content_type).parser == expected


def test_supported_content_types_are_not_expanded():
    assert set(loaders.get_supported_content_types()) == {
        "application/pdf",
        "text/plain",
        "text/markdown",
        "application/msword",
        DOCX,
        "text/html",
        "application/xhtml+xml",
    }
    assert not loaders.is_content_type_supported("application/x-fixture")
