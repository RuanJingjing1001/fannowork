from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from zipfile import ZipFile

from fanno_work.models import (
    ActionRequest,
)

from fanno_legal.backends.document.libreoffice_cli import (
    LibreOfficeBackend,
    LibreOfficeConversion,
    LibreOfficeFailure,
)
from fanno_legal.backends.document.libreoffice_uno import (
    LibreOfficeEdit,
    LibreOfficeUnoBackend,
    LibreOfficeUnoFailure,
)
from fanno_legal.capabilities.document import (
    DocumentCapability,
)
from fanno_legal.capabilities.filesystem import (
    FilesystemCapability,
)
from fanno_legal.state.audit import (
    AuditLog,
)
from fanno_legal.state.database import (
    StateDatabase,
)


FIXTURES = (
    Path(__file__).parents[1]
    / "fixtures"
)


def load_seed(
    name: str,
) -> dict:
    return json.loads(
        (
            FIXTURES / name
        ).read_text(
            encoding="utf-8"
        )
    )


def write_minimal_docx(
    path: Path,
    text: str,
) -> None:
    xml = (
        '<?xml version="1.0" '
        'encoding="UTF-8" '
        'standalone="yes"?>'
        '<w:document '
        'xmlns:w="http://schemas.'
        'openxmlformats.org/'
        'wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>"
        f"{text}"
        "</w:t></w:r></w:p>"
        "</w:body>"
        "</w:document>"
    )

    with ZipFile(
        path,
        "w",
    ) as archive:
        archive.writestr(
            "word/document.xml",
            xml,
        )


def write_minimal_pdf(
    path: Path,
    text: str,
) -> None:
    escaped = (
        text.replace(
            "\\",
            "\\\\",
        )
        .replace(
            "(",
            "\\(",
        )
        .replace(
            ")",
            "\\)",
        )
    )

    stream = (
        "BT /F1 12 Tf "
        "72 720 Td "
        f"({escaped}) Tj ET"
    ).encode("ascii")

    objects = [
        (
            b"<< /Type /Catalog "
            b"/Pages 2 0 R >>"
        ),
        (
            b"<< /Type /Pages "
            b"/Kids [3 0 R] "
            b"/Count 1 >>"
        ),
        (
            b"<< /Type /Page "
            b"/Parent 2 0 R "
            b"/MediaBox [0 0 612 792] "
            b"/Resources << "
            b"/Font << "
            b"/F1 4 0 R "
            b">> >> "
            b"/Contents 5 0 R >>"
        ),
        (
            b"<< /Type /Font "
            b"/Subtype /Type1 "
            b"/BaseFont /Helvetica >>"
        ),
        (
            b"<< /Length "
            + str(
                len(stream)
            ).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        ),
    ]

    document = bytearray(
        b"%PDF-1.4\n"
    )

    offsets = [0]

    for number, value in enumerate(
        objects,
        start=1,
    ):
        offsets.append(
            len(document)
        )

        document.extend(
            f"{number} 0 obj\n".encode(
                "ascii"
            )
        )
        document.extend(value)
        document.extend(
            b"\nendobj\n"
        )

    xref_offset = len(document)

    document.extend(
        (
            "xref\n"
            f"0 {len(objects) + 1}\n"
        ).encode("ascii")
    )

    document.extend(
        b"0000000000 65535 f \n"
    )

    for offset in offsets[1:]:
        document.extend(
            (
                f"{offset:010d} "
                "00000 n \n"
            ).encode("ascii")
        )

    document.extend(
        (
            "trailer\n"
            f"<< /Size {len(objects) + 1} "
            "/Root 1 0 R >>\n"
            "startxref\n"
            f"{xref_offset}\n"
            "%%EOF\n"
        ).encode("ascii")
    )

    path.write_bytes(document)


class FakeLibreOfficeBackend(
    LibreOfficeBackend
):
    def __init__(self) -> None:
        # Do not initialize a real executable.
        pass

    def convert(
        self,
        *,
        source: Path,
        output_dir: Path,
        output_format: str,
    ) -> LibreOfficeConversion:
        output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        generated_path = (
            output_dir
            / f"{source.stem}.{output_format}"
        )

        source_text = source.read_text(
            encoding="utf-8"
        )

        write_minimal_pdf(
            generated_path,
            source_text,
        )

        return LibreOfficeConversion(
            generated_path=generated_path,
            backend="fake-libreoffice",
            backend_version="test-1.0",
            stdout="converted",
            stderr="",
        )


class FailingLibreOfficeBackend(
    LibreOfficeBackend
):
    def __init__(self) -> None:
        pass

    def convert(
        self,
        *,
        source: Path,
        output_dir: Path,
        output_format: str,
    ) -> LibreOfficeConversion:
        raise LibreOfficeFailure(
            "conversion_failed"
        )


class FakeLibreOfficeUnoBackend(
    LibreOfficeUnoBackend
):
    def __init__(self) -> None:
        pass

    def append_paragraph(
        self,
        *,
        source: Path,
        target: Path,
        text: str,
    ) -> LibreOfficeEdit:
        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        shutil.copy2(source, target)

        # A fake backend is sufficient for capability-policy tests. The
        # real Docker integration test independently validates DOCX XML.
        with target.open("ab") as stream:
            stream.write(b"\nFAKE-UNO-PARAGRAPH:")
            stream.write(text.encode("utf-8"))

        content = target.read_bytes()
        return LibreOfficeEdit(
            generated_path=target.resolve(),
            backend="fake-libreoffice-uno",
            backend_version="test-1.0",
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            stdout="edited",
            stderr="",
        )


class FailingLibreOfficeUnoBackend(
    LibreOfficeUnoBackend
):
    def __init__(self) -> None:
        pass

    def append_paragraph(
        self,
        *,
        source: Path,
        target: Path,
        text: str,
    ) -> LibreOfficeEdit:
        raise LibreOfficeUnoFailure(
            "document_edit_failed"
        )


class DocumentCapabilityTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.temporary_directory = (
            tempfile.TemporaryDirectory()
        )

        self.root = Path(
            self.temporary_directory.name
        )

        self.workspace = (
            self.root / "workspace"
        )

        (
            self.workspace / "inputs"
        ).mkdir(parents=True)

        (
            self.workspace / "work"
        ).mkdir()

        (
            self.workspace / "outputs"
        ).mkdir()

        self.database = StateDatabase(
            self.root / "state.db"
        )

        self.database.seed(
            load_seed("seed-a.json")
        )

        self.audit = AuditLog(
            self.root / "audit.jsonl"
        )

        self.filesystem = (
            FilesystemCapability(
                workspace=self.workspace,
                database=self.database,
                audit=self.audit,
                config={
                    "readable_prefixes": [
                        "inputs/",
                        "work/",
                        "outputs/",
                    ],
                    "writable_prefixes": [
                        "work/",
                        "outputs/",
                    ],
                    "max_file_bytes": (
                        1024 * 1024
                    ),
                },
            )
        )

        self.capability = (
            self._capability()
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _capability(
        self,
        *,
        max_file_bytes: int = (
            1024 * 1024
        ),
        max_text_characters: int = (
            10_000
        ),
        backend: (
            LibreOfficeBackend | None
        ) = None,
        uno_backend: (
            LibreOfficeUnoBackend | None
        ) = None,
    ) -> DocumentCapability:
        if backend is None:
            backend = (
                FakeLibreOfficeBackend()
            )

        if uno_backend is None:
            uno_backend = (
                FakeLibreOfficeUnoBackend()
            )

        return DocumentCapability(
            filesystem=self.filesystem,
            database=self.database,
            audit=self.audit,
            config={
                "supported_extensions": [
                    ".txt",
                    ".docx",
                    ".pdf",
                ],
                "max_file_bytes": (
                    max_file_bytes
                ),
                "max_text_characters": (
                    max_text_characters
                ),
                "max_archive_uncompressed_bytes": (
                    1024 * 1024
                ),
                "conversion": {
                    "backend": "libreoffice",
                    "executable": "soffice",
                    "timeout_seconds": 60,
                    "input_extensions": [
                        ".txt",
                        ".docx",
                        ".odt",
                    ],
                    "output_formats": [
                        "pdf",
                    ],
                    "max_output_bytes": (
                        10 * 1024 * 1024
                    ),
                },
                "uno": {
                    "system_python": (
                        "/usr/bin/python3"
                    ),
                    "soffice_executable": (
                        "soffice"
                    ),
                    "timeout_seconds": 60,
                    "max_insert_characters": 20_000,
                    "max_output_bytes": (
                        10 * 1024 * 1024
                    ),
                    "input_extensions": [
                        ".docx",
                        ".odt",
                    ],
                    "output_extensions": [
                        ".docx",
                        ".odt",
                    ],
                },
            },
            libreoffice_backend=backend,
            uno_backend=uno_backend,
        )

    def _extract(
        self,
        path: str,
    ):
        return self.capability.act(
            "agent",
            ActionRequest(
                "document.extract_text",
                {
                    "path": path,
                },
            ),
        )

    def _convert(
        self,
        source_path: str,
        target_path: str,
        output_format: str = "pdf",
    ):
        return self.capability.act(
            "agent",
            ActionRequest(
                "document.convert",
                {
                    "source_path": (
                        source_path
                    ),
                    "target_path": (
                        target_path
                    ),
                    "format": (
                        output_format
                    ),
                },
            ),
        )

    def _append_paragraph(
        self,
        source_path: str,
        target_path: str,
        text: str,
    ):
        return self.capability.act(
            "agent",
            ActionRequest(
                "document.append_paragraph",
                {
                    "source_path": source_path,
                    "target_path": target_path,
                    "text": text,
                },
            ),
        )

    def test_extracts_utf8_text(
        self,
    ) -> None:
        (
            self.workspace
            / "inputs"
            / "brief.txt"
        ).write_text(
            "Legal text evidence",
            encoding="utf-8",
        )

        result = self._extract(
            "inputs/brief.txt"
        )

        self.assertTrue(result.ok)

        self.assertEqual(
            result.output["text"],
            "Legal text evidence",
        )

        self.assertEqual(
            result.output["backend"],
            "utf-8",
        )

        self.assertFalse(
            result.state_changed
        )

    def test_extracts_docx_text(
        self,
    ) -> None:
        write_minimal_docx(
            (
                self.workspace
                / "inputs"
                / "contract.docx"
            ),
            "DOCX legal clause",
        )

        result = self._extract(
            "inputs/contract.docx"
        )

        self.assertTrue(result.ok)

        self.assertEqual(
            result.output["text"],
            "DOCX legal clause",
        )

        self.assertEqual(
            result.output["backend"],
            "docx-xml",
        )

    def test_extracts_pdf_text(
        self,
    ) -> None:
        write_minimal_pdf(
            (
                self.workspace
                / "inputs"
                / "report.pdf"
            ),
            "PDF legal evidence",
        )

        result = self._extract(
            "inputs/report.pdf"
        )

        self.assertTrue(
            result.ok,
            result.error,
        )

        self.assertIn(
            "PDF legal evidence",
            result.output["text"],
        )

        self.assertEqual(
            result.output["page_count"],
            1,
        )

        self.assertEqual(
            result.output["backend"],
            "pypdf",
        )

    def test_rejects_unsupported_format(
        self,
    ) -> None:
        (
            self.workspace
            / "inputs"
            / "table.csv"
        ).write_text(
            "a,b",
            encoding="utf-8",
        )

        result = self._extract(
            "inputs/table.csv"
        )

        self.assertFalse(result.ok)

        self.assertEqual(
            result.error,
            "unsupported_format",
        )

    def test_enforces_workspace_read_policy(
        self,
    ) -> None:
        result = self._extract(
            "private/secret.txt"
        )

        self.assertFalse(result.ok)

        self.assertEqual(
            result.error,
            "permission_denied",
        )

    def test_rejects_large_input(
        self,
    ) -> None:
        (
            self.workspace
            / "inputs"
            / "large.txt"
        ).write_text(
            "12345",
            encoding="utf-8",
        )

        capability = self._capability(
            max_file_bytes=4
        )

        result = capability.act(
            "agent",
            ActionRequest(
                "document.extract_text",
                {
                    "path": (
                        "inputs/large.txt"
                    )
                },
            ),
        )

        self.assertFalse(result.ok)

        self.assertEqual(
            result.error,
            "file_too_large",
        )

    def test_rejects_excessive_text(
        self,
    ) -> None:
        (
            self.workspace
            / "inputs"
            / "long.txt"
        ).write_text(
            "12345",
            encoding="utf-8",
        )

        capability = self._capability(
            max_text_characters=4
        )

        result = capability.act(
            "agent",
            ActionRequest(
                "document.extract_text",
                {
                    "path": (
                        "inputs/long.txt"
                    )
                },
            ),
        )

        self.assertFalse(result.ok)

        self.assertEqual(
            result.error,
            "text_too_large",
        )

    def test_extraction_is_read_only_and_audited(
        self,
    ) -> None:
        (
            self.workspace
            / "inputs"
            / "brief.txt"
        ).write_text(
            "Evidence",
            encoding="utf-8",
        )

        self._extract(
            "inputs/brief.txt"
        )

        event = (
            self.audit.read_all()[0]
        )

        self.assertEqual(
            self.database
            .get_state_version(),
            0,
        )

        self.assertEqual(
            event["action"],
            "document.extract_text",
        )

        self.assertTrue(
            event["ok"]
        )

        self.assertFalse(
            event["state_changed"]
        )

    def test_converts_document_to_pdf(
        self,
    ) -> None:
        (
            self.workspace
            / "inputs"
            / "report.txt"
        ).write_text(
            "Converted legal report",
            encoding="utf-8",
        )

        result = self._convert(
            "inputs/report.txt",
            "outputs/report.pdf",
        )

        target = (
            self.workspace
            / "outputs"
            / "report.pdf"
        )

        self.assertTrue(
            result.ok,
            result.error,
        )

        self.assertTrue(
            target.is_file()
        )

        self.assertTrue(
            target.read_bytes().startswith(
                b"%PDF"
            )
        )

        self.assertTrue(
            result.state_changed
        )

        self.assertTrue(
            result.output["created"]
        )

        self.assertEqual(
            result.output["backend"],
            "fake-libreoffice",
        )

        self.assertEqual(
            self.database
            .get_state_version(),
            1,
        )

    def test_conversion_is_audited(
        self,
    ) -> None:
        (
            self.workspace
            / "inputs"
            / "report.txt"
        ).write_text(
            "Audit evidence",
            encoding="utf-8",
        )

        self._convert(
            "inputs/report.txt",
            "outputs/report.pdf",
        )

        event = (
            self.audit.read_all()[0]
        )

        self.assertEqual(
            event["action"],
            "document.convert",
        )

        self.assertTrue(
            event["ok"]
        )

        self.assertTrue(
            event["state_changed"]
        )

        self.assertEqual(
            event["state_version_before"],
            0,
        )

        self.assertEqual(
            event["state_version_after"],
            1,
        )

    def test_same_conversion_does_not_advance_state_twice(
        self,
    ) -> None:
        (
            self.workspace
            / "inputs"
            / "report.txt"
        ).write_text(
            "Stable legal report",
            encoding="utf-8",
        )

        first = self._convert(
            "inputs/report.txt",
            "outputs/report.pdf",
        )

        second = self._convert(
            "inputs/report.txt",
            "outputs/report.pdf",
        )

        self.assertTrue(first.ok)
        self.assertTrue(second.ok)

        self.assertTrue(
            first.state_changed
        )

        self.assertFalse(
            second.state_changed
        )

        self.assertFalse(
            second.output["created"]
        )

        self.assertEqual(
            self.database
            .get_state_version(),
            1,
        )

    def test_conversion_cannot_write_to_inputs(
        self,
    ) -> None:
        (
            self.workspace
            / "inputs"
            / "report.txt"
        ).write_text(
            "Protected input",
            encoding="utf-8",
        )

        result = self._convert(
            "inputs/report.txt",
            "inputs/report.pdf",
        )

        self.assertFalse(result.ok)

        self.assertEqual(
            result.error,
            "permission_denied",
        )

        self.assertFalse(
            (
                self.workspace
                / "inputs"
                / "report.pdf"
            ).exists()
        )

        self.assertEqual(
            self.database
            .get_state_version(),
            0,
        )

    def test_conversion_requires_matching_extension(
        self,
    ) -> None:
        (
            self.workspace
            / "inputs"
            / "report.txt"
        ).write_text(
            "Wrong target extension",
            encoding="utf-8",
        )

        result = self._convert(
            "inputs/report.txt",
            "outputs/report.docx",
            "pdf",
        )

        self.assertFalse(result.ok)

        self.assertEqual(
            result.error,
            "invalid_target_extension",
        )

    def test_conversion_failure_does_not_change_state(
        self,
    ) -> None:
        (
            self.workspace
            / "inputs"
            / "report.txt"
        ).write_text(
            "Conversion should fail",
            encoding="utf-8",
        )

        capability = self._capability(
            backend=(
                FailingLibreOfficeBackend()
            )
        )

        result = capability.act(
            "agent",
            ActionRequest(
                "document.convert",
                {
                    "source_path": (
                        "inputs/report.txt"
                    ),
                    "target_path": (
                        "outputs/report.pdf"
                    ),
                    "format": "pdf",
                },
            ),
        )

        self.assertFalse(result.ok)

        self.assertEqual(
            result.error,
            "conversion_failed",
        )

        self.assertEqual(
            self.database
            .get_state_version(),
            0,
        )

        self.assertFalse(
            (
                self.workspace
                / "outputs"
                / "report.pdf"
            ).exists()
        )

    def test_append_paragraph_creates_output_and_audit(
        self,
    ) -> None:
        source = (
            self.workspace
            / "inputs"
            / "source.docx"
        )
        write_minimal_docx(
            source,
            "Original legal text",
        )
        source_before = source.read_bytes()

        result = self._append_paragraph(
            "inputs/source.docx",
            "outputs/edited.docx",
            "Added legal approval paragraph.",
        )

        target = (
            self.workspace
            / "outputs"
            / "edited.docx"
        )
        self.assertTrue(result.ok, result.error)
        self.assertTrue(result.state_changed)
        self.assertTrue(target.is_file())
        self.assertEqual(source.read_bytes(), source_before)
        self.assertEqual(
            result.output["backend"],
            "fake-libreoffice-uno",
        )
        self.assertEqual(
            self.database.get_state_version(),
            1,
        )

        event = self.audit.read_all()[0]
        self.assertEqual(
            event["action"],
            "document.append_paragraph",
        )
        self.assertTrue(event["ok"])
        self.assertNotIn("text", event["arguments"])
        self.assertEqual(
            event["arguments"]["text_character_count"],
            len("Added legal approval paragraph."),
        )
        self.assertIn("text_sha256", event["arguments"])

    def test_append_paragraph_cannot_write_to_inputs(
        self,
    ) -> None:
        source = self.workspace / "inputs" / "source.docx"
        write_minimal_docx(source, "Protected legal text")

        result = self._append_paragraph(
            "inputs/source.docx",
            "inputs/edited.docx",
            "This must not be written.",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "permission_denied")
        self.assertFalse(
            (self.workspace / "inputs" / "edited.docx").exists()
        )
        self.assertEqual(self.database.get_state_version(), 0)

    def test_append_paragraph_rejects_empty_text(
        self,
    ) -> None:
        source = self.workspace / "inputs" / "source.docx"
        write_minimal_docx(source, "Original legal text")

        result = self._append_paragraph(
            "inputs/source.docx",
            "outputs/edited.docx",
            "   ",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "invalid_text")
        self.assertEqual(self.database.get_state_version(), 0)

    def test_append_failure_leaves_no_partial_output(
        self,
    ) -> None:
        source = self.workspace / "inputs" / "source.docx"
        write_minimal_docx(source, "Original legal text")
        capability = self._capability(
            uno_backend=FailingLibreOfficeUnoBackend()
        )

        result = capability.act(
            "agent",
            ActionRequest(
                "document.append_paragraph",
                {
                    "source_path": "inputs/source.docx",
                    "target_path": "outputs/edited.docx",
                    "text": "This edit fails.",
                },
            ),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "document_edit_failed")
        self.assertFalse(
            (self.workspace / "outputs" / "edited.docx").exists()
        )
        self.assertEqual(self.database.get_state_version(), 0)


if __name__ == "__main__":
    unittest.main()
