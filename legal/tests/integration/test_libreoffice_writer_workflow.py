from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from fanno_legal.backends.document.libreoffice_uno import (
    LibreOfficeUnoBackend,
)


def uno_is_available() -> bool:
    if not Path(
        "/usr/bin/python3"
    ).is_file():
        return False

    if shutil.which("soffice") is None:
        return False

    try:
        completed = subprocess.run(
            [
                "/usr/bin/python3",
                "-c",
                "import uno",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except (
        OSError,
        subprocess.TimeoutExpired,
    ):
        return False

    return completed.returncode == 0


def docx_text(
    path: Path,
) -> str:
    namespace = (
        "{http://schemas.openxmlformats.org/"
        "wordprocessingml/2006/main}"
    )

    with ZipFile(path) as archive:
        xml = archive.read(
            "word/document.xml"
        )

    root = ET.fromstring(xml)

    return "\n".join(
        node.text or ""
        for node in root.iter(
            f"{namespace}t"
        )
    )


def docx_table_count(
    path: Path,
) -> int:
    namespace = (
        "{http://schemas.openxmlformats.org/"
        "wordprocessingml/2006/main}"
    )

    with ZipFile(path) as archive:
        xml = archive.read(
            "word/document.xml"
        )

    root = ET.fromstring(xml)

    return sum(
        1
        for _ in root.iter(
            f"{namespace}tbl"
        )
    )


@unittest.skipUnless(
    uno_is_available(),
    "LibreOffice Writer PyUNO is unavailable",
)
class LibreOfficeWriterWorkflowTests(
    unittest.TestCase
):
    def test_complete_writer_workflow(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            empty_document = (
                root / "01-empty.odt"
            )
            with_heading = (
                root / "02-heading.odt"
            )
            with_paragraph = (
                root / "03-paragraph.odt"
            )
            with_table = (
                root / "04-table.odt"
            )
            final_docx = (
                root / "review-memo.docx"
            )

            backend = (
                LibreOfficeUnoBackend(
                    system_python=(
                        "/usr/bin/python3"
                    ),
                    soffice_executable=(
                        "soffice"
                    ),
                    timeout_seconds=60,
                )
            )

            create_result = (
                backend.create_document(
                    target=empty_document,
                    title=(
                        "Litigation Invoice "
                        "Research Charge Review"
                    ),
                )
            )

            self.assertTrue(
                empty_document.is_file()
            )
            self.assertEqual(
                create_result.backend,
                "libreoffice-writer-uno",
            )
            self.assertEqual(
                create_result
                .execution_evidence[
                    "transport"
                ],
                "uno_pipe",
            )
            self.assertIsInstance(
                create_result
                .execution_evidence[
                    "connect_ms"
                ],
                float,
            )
            self.assertIsInstance(
                create_result
                .execution_evidence[
                    "open_ms"
                ],
                float,
            )
            self.assertIsInstance(
                create_result
                .execution_evidence[
                    "operation_ms"
                ],
                float,
            )

            backend.insert_heading(
                source=empty_document,
                target=with_heading,
                text="Executive Summary",
                level=1,
            )

            backend.insert_paragraph(
                source=with_heading,
                target=with_paragraph,
                text=(
                    "The July invoice contains "
                    "supported billing issues."
                ),
                style="Body Text",
            )

            backend.add_table(
                source=with_paragraph,
                target=with_table,
                headers=[
                    "Issue",
                    "Guideline",
                    "Adjustment",
                ],
                rows=[
                    [
                        "Westlaw disbursement",
                        "Section 9.3",
                        "$4,850.00",
                    ],
                    [
                        "Research cap",
                        "Section 6.2",
                        "$5,354.04",
                    ],
                ],
            )

            save_result = backend.save_as(
                source=with_table,
                target=final_docx,
            )

            self.assertTrue(
                final_docx.is_file()
            )
            self.assertGreater(
                final_docx.stat().st_size,
                0,
            )
            self.assertEqual(
                save_result.backend,
                "libreoffice-writer-uno",
            )
            self.assertGreater(
                save_result
                .execution_evidence[
                    "total_ms"
                ],
                0,
            )

            text = docx_text(
                final_docx
            )

            self.assertIn(
                "Executive Summary",
                text,
            )
            self.assertIn(
                (
                    "The July invoice contains "
                    "supported billing issues."
                ),
                text,
            )
            self.assertIn(
                "Westlaw disbursement",
                text,
            )
            self.assertIn(
                "$4,850.00",
                text,
            )
            self.assertEqual(
                docx_table_count(
                    final_docx
                ),
                1,
            )


if __name__ == "__main__":
    unittest.main()
