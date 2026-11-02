from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from fanno_work.backends import BackendFailure, execution_evidence


class TextExtractionFailure(BackendFailure):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(
            code,
            detail,
            backend="text-extractor",
        )


@dataclass(frozen=True)
class ExtractedText:
    text: str
    page_count: int | None
    backend: str
    warnings: tuple[str, ...] = ()
    execution_evidence: dict[str, object] = field(
        default_factory=dict
    )


class TextExtractor:
    """Deterministic TXT, DOCX and text-PDF extraction backend."""

    def __init__(self, *, max_archive_uncompressed_bytes: int) -> None:
        if (
            not isinstance(max_archive_uncompressed_bytes, int)
            or isinstance(max_archive_uncompressed_bytes, bool)
            or max_archive_uncompressed_bytes <= 0
        ):
            raise ValueError(
                "max_archive_uncompressed_bytes must be a positive integer"
            )
        self.max_archive_uncompressed_bytes = (
            max_archive_uncompressed_bytes
        )

    def extract(self, path: Path) -> ExtractedText:
        extension = Path(path).suffix.lower()
        if extension == ".txt":
            return self._extract_txt(path)
        if extension == ".docx":
            return self._extract_docx(path)
        if extension == ".pdf":
            return self._extract_pdf(path)
        raise TextExtractionFailure("unsupported_format")

    @staticmethod
    def _extract_txt(path: Path) -> ExtractedText:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise TextExtractionFailure("unsupported_encoding") from exc
        except OSError as exc:
            raise TextExtractionFailure("extraction_failed") from exc

        return ExtractedText(
            text=text,
            page_count=None,
            backend="utf-8",
            execution_evidence=execution_evidence(
                backend="utf-8",
                execution_scope="controller_process",
                timeout_seconds=None,
                runtime="python",
            ),
        )

    def _extract_docx(self, path: Path) -> ExtractedText:
        namespace = (
            "{http://schemas.openxmlformats.org/"
            "wordprocessingml/2006/main}"
        )

        try:
            with ZipFile(path) as archive:
                info = archive.getinfo("word/document.xml")
                if info.file_size > self.max_archive_uncompressed_bytes:
                    raise TextExtractionFailure("file_too_large")
                document_xml = archive.read(info)
        except TextExtractionFailure:
            raise
        except (BadZipFile, KeyError, OSError) as exc:
            raise TextExtractionFailure("extraction_failed") from exc

        try:
            root = ET.fromstring(document_xml)
        except ET.ParseError as exc:
            raise TextExtractionFailure("extraction_failed") from exc

        paragraphs: list[str] = []
        for paragraph in root.iter(f"{namespace}p"):
            paragraph_text = "".join(
                node.text or ""
                for node in paragraph.iter(f"{namespace}t")
            )
            if paragraph_text:
                paragraphs.append(paragraph_text)

        return ExtractedText(
            text="\n".join(paragraphs),
            page_count=None,
            backend="docx-xml",
            execution_evidence=execution_evidence(
                backend="docx-xml",
                execution_scope="controller_process",
                timeout_seconds=None,
                runtime="python",
            ),
        )

    @staticmethod
    def _extract_pdf(path: Path) -> ExtractedText:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise TextExtractionFailure("backend_unavailable") from exc

        try:
            reader = PdfReader(str(path), strict=False)
            if reader.is_encrypted:
                raise TextExtractionFailure("encrypted_document")

            page_texts: list[str] = []
            empty_pages: list[int] = []
            for index, page in enumerate(reader.pages, start=1):
                page_text = page.extract_text() or ""
                page_texts.append(page_text)
                if not page_text.strip():
                    empty_pages.append(index)
        except TextExtractionFailure:
            raise
        except Exception as exc:
            raise TextExtractionFailure("extraction_failed") from exc

        return ExtractedText(
            text="\n\n".join(page_texts),
            page_count=len(page_texts),
            backend="pypdf",
            warnings=tuple(
                f"no_extractable_text_on_page:{page}"
                for page in empty_pages
            ),
            execution_evidence=execution_evidence(
                backend="pypdf",
                execution_scope="controller_process",
                timeout_seconds=None,
                runtime="python",
            ),
        )

