from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from legal.examples.run_document_uno_demo import (
    APPENDED_TEXT,
    extract_docx_text,
    run_demo,
    uno_is_available,
)


LEGAL_ROOT = Path(__file__).parents[2]
ENVIRONMENT = (
    LEGAL_ROOT
    / "environments"
    / "legal-office"
    / "v1"
    / "environment.json"
)
TASK = (
    LEGAL_ROOT
    / "tasks"
    / "examples"
    / "document-append"
    / "v1"
    / "task.json"
)


@unittest.skipUnless(
    uno_is_available(),
    "LibreOffice PyUNO is not available",
)
class DocumentUnoEnvironmentIntegrationTests(unittest.TestCase):
    def test_complete_environment_edit_is_inspectable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "document-demo"
            manifest = run_demo(
                environment_path=ENVIRONMENT,
                task_path=TASK,
                output_dir=output_dir,
            )

            self.assertEqual(manifest["status"], "passed")
            self.assertEqual(manifest["action_count"], 4)
            self.assertTrue(manifest["verification"]["passed"])
            self.assertTrue(manifest["reset"]["passed"])

            artifacts = output_dir / "run" / "artifacts"
            edited_docx = artifacts / "edited_contract.docx"
            self.assertTrue(edited_docx.is_file())
            self.assertTrue((artifacts / "original_contract.pdf").is_file())
            self.assertTrue((artifacts / "edited_contract.pdf").is_file())
            self.assertIn(APPENDED_TEXT, extract_docx_text(edited_docx))

            verification = json.loads(
                (output_dir / "VERIFICATION.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(verification["source_unchanged"])
            self.assertTrue(verification["appended_text_found"])
            self.assertTrue(verification["reset_passed"])
            self.assertTrue((output_dir / "REPORT.html").is_file())
            self.assertTrue((output_dir / "REPORT.md").is_file())
            self.assertTrue((output_dir / "ACTION_RESULTS.json").is_file())


if __name__ == "__main__":
    unittest.main()
