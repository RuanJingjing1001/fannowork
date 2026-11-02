from __future__ import annotations

import hashlib
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


def write_valid_docx(
    path: Path,
    text: str,
) -> None:
    content_types = (
        '<?xml version="1.0" '
        'encoding="UTF-8"?>'
        '<Types '
        'xmlns="http://schemas.openxmlformats.org/'
        'package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-'
        'package.relationships+xml"/>'
        '<Default Extension="xml" '
        'ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.document.'
        'main+xml"/>'
        "</Types>"
    )

    relationships = (
        '<?xml version="1.0" '
        'encoding="UTF-8"?>'
        '<Relationships '
        'xmlns="http://schemas.openxmlformats.org/'
        'package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/'
        'officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )

    document = (
        '<?xml version="1.0" '
        'encoding="UTF-8" standalone="yes"?>'
        '<w:document '
        'xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>"
        f"{text}"
        "</w:t></w:r></w:p>"
        "<w:sectPr>"
        '<w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1440" '
        'w:bottom="1440" w:left="1440"/>'
        "</w:sectPr>"
        "</w:body>"
        "</w:document>"
    )

    with ZipFile(
        path,
        "w",
    ) as archive:
        archive.writestr(
            "[Content_Types].xml",
            content_types,
        )
        archive.writestr(
            "_rels/.rels",
            relationships,
        )
        archive.writestr(
            "word/document.xml",
            document,
        )


def extract_docx_text(
    path: Path,
) -> str:
    namespace = (
        "{http://schemas.openxmlformats.org/"
        "wordprocessingml/2006/main}"
    )

    with ZipFile(path) as archive:
        document_xml = archive.read(
            "word/document.xml"
        )

    root = ET.fromstring(
        document_xml
    )

    return "\n".join(
        node.text or ""
        for node in root.iter(
            f"{namespace}t"
        )
    )


@unittest.skipUnless(
    uno_is_available(),
    "LibreOffice PyUNO is not available",
)
class LibreOfficeUnoBackendTests(
    unittest.TestCase
):
    def test_real_writer_appends_paragraph(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            source = (
                root / "source.docx"
            )
            target = (
                root / "edited.docx"
            )

            write_valid_docx(
                source,
                "Original contract text.",
            )

            source_hash_before = (
                hashlib.sha256(
                    source.read_bytes()
                ).hexdigest()
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

            result = (
                backend.append_paragraph(
                    source=source,
                    target=target,
                    text=(
                        "This agreement is "
                        "subject to final "
                        "legal approval."
                    ),
                )
            )

            source_hash_after = (
                hashlib.sha256(
                    source.read_bytes()
                ).hexdigest()
            )

            self.assertEqual(
                source_hash_after,
                source_hash_before,
            )

            self.assertTrue(
                target.is_file()
            )

            self.assertGreater(
                target.stat().st_size,
                0,
            )

            self.assertEqual(
                result.generated_path,
                target.resolve(),
            )

            self.assertEqual(
                result.backend,
            "libreoffice-writer-uno",
            )

            independent_text = (
                extract_docx_text(
                    target
                )
            )

            self.assertIn(
                "Original contract text.",
                independent_text,
            )

            self.assertIn(
                (
                    "This agreement is "
                    "subject to final "
                    "legal approval."
                ),
                independent_text,
            )


if __name__ == "__main__":
    unittest.main()
