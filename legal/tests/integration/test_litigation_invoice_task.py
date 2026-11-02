from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from fanno_work.models import ActionRequest
from fanno_work.specs import (
    load_environment,
    load_task,
)

from fanno_legal.runtime.environment import (
    LegalOfficeEnvironment,
)


LEGAL_ROOT = Path(__file__).parents[2]

ENVIRONMENT_PATH = (
    LEGAL_ROOT
    / "environments"
    / "legal-office"
    / "v1"
    / "environment.json"
)

TASK_PATH = (
    LEGAL_ROOT
    / "tasks"
    / "imported"
    / "harvey-lab"
    / "litigation-invoice-review"
    / "v1"
    / "task.json"
)

EXPECTED_INPUTS = {
    "doe-matter-july-2024-summary.docx",
    "engagement-letter-cascade.docx",
    "hl-july-2024-invoice.xlsx",
    "hl-june-2024-invoice-summary.docx",
    "terraverde-billing-guidelines.docx",
}

DOCX_INPUTS = (
    "doe-matter-july-2024-summary.docx",
    "engagement-letter-cascade.docx",
    "hl-june-2024-invoice-summary.docx",
    "terraverde-billing-guidelines.docx",
)


def calc_uno_is_available() -> bool:
    """Return whether the real LibreOffice Calc backend can run."""
    if not Path("/usr/bin/python3").is_file():
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


def file_sha256(path: Path) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def prepare_environment(
    run_dir: Path,
) -> tuple[
    object,
    LegalOfficeEnvironment,
]:
    task = load_task(
        TASK_PATH,
        verify_assets=True,
    )
    environment_spec = load_environment(
        ENVIRONMENT_PATH
    )

    environment = LegalOfficeEnvironment(
        environment_spec
    )
    environment.prepare(
        task,
        run_dir,
    )

    return task, environment


class LitigationInvoiceTaskPackageTests(
    unittest.TestCase
):
    """Tests that do not require LibreOffice UNO."""

    def setUp(self) -> None:
        self.temporary_directory = (
            tempfile.TemporaryDirectory()
        )
        self.root = Path(
            self.temporary_directory.name
        )
        self.run_dir = self.root / "run"

        (
            self.task,
            self.environment,
        ) = prepare_environment(
            self.run_dir
        )

        self.workspace = (
            self.run_dir / "workspace"
        )
        self.inputs_dir = (
            self.workspace / "inputs"
        )

    def tearDown(self) -> None:
        self.environment.close()
        self.temporary_directory.cleanup()

    def test_task_spec_loads_and_verifies_assets(
        self,
    ) -> None:
        self.assertEqual(
            self.task.id,
            (
                "legal.harvey-lab."
                "litigation-invoice-review.v1"
            ),
        )
        self.assertEqual(
            self.task.environment_id,
            "legal.office.v1",
        )
        self.assertEqual(
            len(self.task.inputs),
            5,
        )
        self.assertEqual(
            len(self.task.outputs),
            1,
        )
        self.assertEqual(
            self.task.outputs[0].path,
            "research-charge-review-memo.docx",
        )
        self.assertEqual(
            self.task.verifier.adapter,
            "required_artifacts",
        )

    def test_all_five_inputs_are_materialized(
        self,
    ) -> None:
        result = self.environment.act(
            ActionRequest(
                name="filesystem.list",
                arguments={
                    "path": "inputs",
                },
            )
        )

        self.assertTrue(
            result.ok,
            result.error,
        )

        visible_files = {
            Path(item["path"]).name
            for item in result.output["entries"]
            if item["kind"] == "file"
        }

        self.assertEqual(
            visible_files,
            EXPECTED_INPUTS,
        )

        disk_files = {
            path.name
            for path in self.inputs_dir.iterdir()
            if path.is_file()
        }

        self.assertEqual(
            disk_files,
            EXPECTED_INPUTS,
        )

    def test_materialized_inputs_match_declared_hashes(
        self,
    ) -> None:
        for artifact in self.task.inputs:
            with self.subTest(
                artifact=artifact.path
            ):
                path = (
                    self.inputs_dir
                    / artifact.path
                )

                self.assertTrue(
                    path.is_file(),
                    artifact.path,
                )
                self.assertIsNotNone(
                    artifact.sha256
                )
                self.assertEqual(
                    file_sha256(path),
                    artifact.sha256,
                )

    def test_all_docx_inputs_can_be_extracted(
        self,
    ) -> None:
        for filename in DOCX_INPUTS:
            with self.subTest(
                filename=filename
            ):
                result = self.environment.act(
                    ActionRequest(
                        name=(
                            "document.extract_text"
                        ),
                        arguments={
                            "path": (
                                f"inputs/{filename}"
                            ),
                        },
                    )
                )

                self.assertTrue(
                    result.ok,
                    result.error,
                )
                self.assertEqual(
                    result.output["path"],
                    f"inputs/{filename}",
                )
                self.assertGreater(
                    result.output[
                        "character_count"
                    ],
                    100,
                )
                self.assertTrue(
                    result.output["text"].strip()
                )
                self.assertFalse(
                    result.state_changed
                )
    def test_email_source_is_not_exposed_as_input_file(
        self,
    ) -> None:
        source_email = (
            TASK_PATH.parent
            / "assets"
            / "holt-email-research-concerns.eml"
        )

        workspace_email = (
            self.inputs_dir
            / "holt-email-research-concerns.eml"
        )

        self.assertTrue(
            source_email.is_file()
        )
        self.assertFalse(
            workspace_email.exists()
        )

        self.assertIn(
            "email",
            self.task.initial_state,
        )
        self.assertEqual(
            len(
                self.task.initial_state[
                    "email"
                ]["messages"]
            ),
            1,
        )
    
    def test_agent_cannot_overwrite_input_file(
        self,
    ) -> None:
        target = (
            self.inputs_dir
            / "hl-july-2024-invoice.xlsx"
        )
        before_hash = file_sha256(target)
        before_version = (
            self.environment
            .snapshot()["state_version"]
        )

        result = self.environment.act(
            ActionRequest(
                name="filesystem.write",
                arguments={
                    "path": (
                        "inputs/"
                        "hl-july-2024-invoice.xlsx"
                    ),
                    "content": "tampered",
                },
            )
        )

        after_hash = file_sha256(target)
        after_version = (
            self.environment
            .snapshot()["state_version"]
        )

        self.assertFalse(result.ok)
        self.assertEqual(
            result.error,
            "permission_denied",
        )
        self.assertEqual(
            after_hash,
            before_hash,
        )
        self.assertEqual(
            after_version,
            before_version,
        )

    def test_reset_restores_workspace_and_input_hashes(
        self,
    ) -> None:
        initial_snapshot = (
            self.environment.snapshot()
        )

        initial_input_hashes = {
            artifact.path: file_sha256(
                self.inputs_dir
                / artifact.path
            )
            for artifact in self.task.inputs
        }

        write_result = self.environment.act(
            ActionRequest(
                name="filesystem.write",
                arguments={
                    "path": (
                        "outputs/"
                        "temporary-analysis.txt"
                    ),
                    "content": (
                        "Temporary test output.\n"
                    ),
                },
            )
        )

        self.assertTrue(
            write_result.ok,
            write_result.error,
        )
        self.assertTrue(
            write_result.state_changed
        )

        temporary_output = (
            self.workspace
            / "outputs"
            / "temporary-analysis.txt"
        )

        self.assertTrue(
            temporary_output.is_file()
        )
        self.assertNotEqual(
            self.environment.snapshot()[
                "digest"
            ],
            initial_snapshot["digest"],
        )

        self.environment.reset()

        restored_snapshot = (
            self.environment.snapshot()
        )

        self.assertFalse(
            temporary_output.exists()
        )
        self.assertEqual(
            restored_snapshot["digest"],
            initial_snapshot["digest"],
        )
        self.assertEqual(
            restored_snapshot[
                "state_version"
            ],
            0,
        )

        restored_input_hashes = {
            artifact.path: file_sha256(
                self.inputs_dir
                / artifact.path
            )
            for artifact in self.task.inputs
        }

        self.assertEqual(
            restored_input_hashes,
            initial_input_hashes,
        )


@unittest.skipUnless(
    calc_uno_is_available(),
    "LibreOffice Calc PyUNO is unavailable",
)
class LitigationInvoiceSpreadsheetTests(
    unittest.TestCase
):
    """Integration tests requiring real LibreOffice Calc."""

    def setUp(self) -> None:
        self.temporary_directory = (
            tempfile.TemporaryDirectory()
        )
        self.root = Path(
            self.temporary_directory.name
        )
        self.run_dir = self.root / "run"

        (
            self.task,
            self.environment,
        ) = prepare_environment(
            self.run_dir
        )

        self.invoice_path = (
            "inputs/"
            "hl-july-2024-invoice.xlsx"
        )

    def tearDown(self) -> None:
        self.environment.close()
        self.temporary_directory.cleanup()

    def test_inspect_real_invoice_workbook(
        self,
    ) -> None:
        result = self.environment.act(
            ActionRequest(
                name=(
                    "spreadsheet."
                    "inspect_workbook"
                ),
                arguments={
                    "path": self.invoice_path,
                },
            )
        )

        self.assertTrue(
            result.ok,
            result.error,
        )
        self.assertEqual(
            result.output["backend"],
            "libreoffice-calc-uno",
        )

        sheet_names = [
            sheet["name"]
            for sheet in result.output["sheets"]
        ]

        self.assertEqual(
            sheet_names,
            [
                "Summary",
                "Fee Detail",
                "Disbursements",
                "Discount",
            ],
        )
        self.assertFalse(
            result.state_changed
        )

    def test_read_real_invoice_fee_range(
        self,
    ) -> None:
        result = self.environment.act(
            ActionRequest(
                name=(
                    "spreadsheet.read_range"
                ),
                arguments={
                    "path": self.invoice_path,
                    "sheet": "Fee Detail",
                    "range": "A1:I3",
                },
            )
        )

        self.assertTrue(
            result.ok,
            result.error,
        )
        self.assertEqual(
            result.output["rows"],
            3,
        )
        self.assertEqual(
            result.output["columns"],
            9,
        )

        first_row = (
            result.output["cells"][0]
        )

        self.assertEqual(
            first_row[0]["display_text"],
            "Line",
        )
        self.assertEqual(
            first_row[2]["display_text"],
            "Timekeeper",
        )
        self.assertEqual(
            first_row[8]["display_text"],
            "Task Description",
        )
        self.assertFalse(
            result.state_changed
        )

    def test_find_westlaw_in_real_invoice(
        self,
    ) -> None:
        result = self.environment.act(
            ActionRequest(
                name="spreadsheet.find",
                arguments={
                    "path": self.invoice_path,
                    "query": "Westlaw",
                    "case_sensitive": False,
                    "max_results": 20,
                },
            )
        )

        self.assertTrue(
            result.ok,
            result.error,
        )
        self.assertGreaterEqual(
            result.output["total_matches"],
            1,
        )
        self.assertTrue(
            any(
                "Westlaw"
                in match["display_text"]
                for match
                in result.output["matches"]
            )
        )
        self.assertFalse(
            result.state_changed
        )


if __name__ == "__main__":
    unittest.main()