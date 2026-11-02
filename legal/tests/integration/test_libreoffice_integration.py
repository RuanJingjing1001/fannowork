from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from fanno_work.models import ActionRequest

from fanno_legal.capabilities.document import (
    DocumentCapability,
)
from fanno_legal.capabilities.filesystem import (
    FilesystemCapability,
)
from fanno_legal.state.audit import AuditLog
from fanno_legal.state.database import (
    StateDatabase,
)


FIXTURES = Path(__file__).parents[1] / "fixtures"


@unittest.skipUnless(
    shutil.which("soffice"),
    "LibreOffice soffice is not installed",
)
class LibreOfficeIntegrationTests(
    unittest.TestCase
):
    def test_real_libreoffice_converts_txt_to_pdf(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"

            (workspace / "inputs").mkdir(
                parents=True
            )
            (workspace / "work").mkdir()
            (workspace / "outputs").mkdir()

            (
                workspace
                / "inputs"
                / "legal-report.txt"
            ).write_text(
                "Legal workflow conversion test.",
                encoding="utf-8",
            )

            seed = json.loads(
                (
                    FIXTURES / "seed-a.json"
                ).read_text(
                    encoding="utf-8"
                )
            )

            database = StateDatabase(
                root / "state.db"
            )
            database.seed(seed)

            audit = AuditLog(
                root / "audit.jsonl"
            )

            filesystem = FilesystemCapability(
                workspace=workspace,
                database=database,
                audit=audit,
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
                        10 * 1024 * 1024
                    ),
                },
            )

            document = DocumentCapability(
                filesystem=filesystem,
                database=database,
                audit=audit,
                config={
                    "supported_extensions": [
                        ".txt",
                        ".docx",
                        ".pdf",
                    ],
                    "max_file_bytes": (
                        20 * 1024 * 1024
                    ),
                    "max_text_characters": 200_000,
                    "max_archive_uncompressed_bytes": (
                        50 * 1024 * 1024
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
                            50 * 1024 * 1024
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
                            50 * 1024 * 1024
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
            )

            result = document.act(
                "agent",
                ActionRequest(
                    "document.convert",
                    {
                        "source_path": (
                            "inputs/legal-report.txt"
                        ),
                        "target_path": (
                            "outputs/legal-report.pdf"
                        ),
                        "format": "pdf",
                    },
                ),
            )

            self.assertTrue(
                result.ok,
                result.error,
            )
            self.assertTrue(
                (
                    workspace
                    / "outputs"
                    / "legal-report.pdf"
                ).is_file()
            )
            self.assertEqual(
                database.get_state_version(),
                1,
            )
            self.assertEqual(
                result.output["backend"],
                "libreoffice-headless",
            )

            verification = document.act(
                "evaluator",
                ActionRequest(
                    "document.extract_text",
                    {
                        "path": (
                            "outputs/legal-report.pdf"
                        )
                    },
                ),
            )

            self.assertTrue(
                verification.ok,
                verification.error,
            )
            self.assertIn(
                "Legal workflow conversion test",
                verification.output["text"],
            )


if __name__ == "__main__":
    unittest.main()
