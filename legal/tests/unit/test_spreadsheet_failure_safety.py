from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from fanno_work.models import (
    ActionRequest,
)

from fanno_legal.backends.spreadsheet.libreoffice_calc import (
    LibreOfficeCalcBackend,
    LibreOfficeCalcFailure,
)
from fanno_legal.capabilities.filesystem import (
    FilesystemCapability,
)
from fanno_legal.capabilities.spreadsheet import (
    SpreadsheetCapability,
)
from fanno_legal.state.audit import AuditLog
from fanno_legal.state.database import (
    StateDatabase,
)
from fanno_legal.state.snapshot import (
    build_environment_snapshot,
)


FIXTURES = (
    Path(__file__).parents[1]
    / "fixtures"
)


def load_seed() -> dict:
    return json.loads(
        (
            FIXTURES / "seed-a.json"
        ).read_text(encoding="utf-8")
    )


class InjectedCalcFailureBackend(
    LibreOfficeCalcBackend
):
    def __init__(
        self,
        code: str,
    ) -> None:
        self.code = code

    def _fail(self):
        raise LibreOfficeCalcFailure(
            self.code
        )

    def inspect_workbook(
        self,
        **kwargs,
    ):
        del kwargs
        return self._fail()

    def read_range(
        self,
        **kwargs,
    ):
        del kwargs
        return self._fail()

    def find(
        self,
        **kwargs,
    ):
        del kwargs
        return self._fail()


class SpreadsheetFailureSafetyTests(
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

        for name in (
            "inputs",
            "work",
            "outputs",
        ):
            (
                self.workspace / name
            ).mkdir(parents=True)

        self.workbook = (
            self.workspace
            / "inputs"
            / "invoice.xlsx"
        )
        self.workbook.write_bytes(
            b"test workbook bytes"
        )

        (
            self.workspace
            / "inputs"
            / "note.txt"
        ).write_text(
            "not a workbook",
            encoding="utf-8",
        )

        self.database = StateDatabase(
            self.root / "state.db"
        )
        self.database.seed(load_seed())

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

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def make_capability(
        self,
        backend: LibreOfficeCalcBackend,
        *,
        max_file_bytes: int = (
            1024 * 1024
        ),
    ) -> SpreadsheetCapability:
        return SpreadsheetCapability(
            filesystem=self.filesystem,
            database=self.database,
            audit=self.audit,
            backend=backend,
            config={
                "supported_extensions": [
                    ".xlsx",
                    ".ods",
                ],
                "max_file_bytes": (
                    max_file_bytes
                ),
                "max_range_cells": 100,
                "max_scan_cells": 1000,
                "max_query_characters": 32,
                "max_results": 20,
                "uno": {
                    "system_python": (
                        "/usr/bin/python3"
                    ),
                    "soffice_executable": (
                        "soffice"
                    ),
                    "timeout_seconds": 60,
                },
            },
        )

    def snapshot(self) -> dict:
        return build_environment_snapshot(
            self.database,
            self.workspace,
        )

    def assert_failed_without_state_change(
        self,
        capability: SpreadsheetCapability,
        request: ActionRequest,
        expected_error: str,
    ) -> None:
        before = self.snapshot()
        version_before = (
            self.database.get_state_version()
        )
        workbook_hash_before = (
            hashlib.sha256(
                self.workbook.read_bytes()
            ).hexdigest()
        )

        result = capability.act(
            "agent",
            request,
        )

        after = self.snapshot()
        workbook_hash_after = (
            hashlib.sha256(
                self.workbook.read_bytes()
            ).hexdigest()
        )

        self.assertFalse(result.ok)
        self.assertEqual(
            result.error,
            expected_error,
        )
        self.assertFalse(
            result.state_changed
        )
        self.assertEqual(
            self.database.get_state_version(),
            version_before,
        )
        self.assertEqual(
            workbook_hash_after,
            workbook_hash_before,
        )
        self.assertEqual(
            after["digest"],
            before["digest"],
        )

        event = self.audit.read_all()[-1]
        self.assertEqual(
            event["action"],
            request.name,
        )
        self.assertFalse(event["ok"])
        self.assertEqual(
            event["error"],
            expected_error,
        )
        self.assertEqual(
            event["state_version_before"],
            event["state_version_after"],
        )

    def test_backend_failures_are_mapped_without_state_change(
        self,
    ) -> None:
        cases = [
            (
                "uno_timeout",
                ActionRequest(
                    "spreadsheet.inspect_workbook",
                    {
                        "path": (
                            "inputs/invoice.xlsx"
                        )
                    },
                ),
            ),
            (
                "sheet_not_found",
                ActionRequest(
                    "spreadsheet.read_range",
                    {
                        "path": (
                            "inputs/invoice.xlsx"
                        ),
                        "sheet": "Missing Sheet",
                        "range": "A1:B2",
                    },
                ),
            ),
            (
                "invalid_worker_response",
                ActionRequest(
                    "spreadsheet.find",
                    {
                        "path": (
                            "inputs/invoice.xlsx"
                        ),
                        "query": "research",
                        "case_sensitive": False,
                        "max_results": 10,
                    },
                ),
            ),
            (
                "range_too_large",
                ActionRequest(
                    "spreadsheet.read_range",
                    {
                        "path": (
                            "inputs/invoice.xlsx"
                        ),
                        "sheet": "Fee Detail",
                        "range": "A1:Z1000",
                    },
                ),
            ),
        ]

        for error, request in cases:
            with self.subTest(
                error=error,
                action=request.name,
            ):
                capability = self.make_capability(
                    InjectedCalcFailureBackend(
                        error
                    )
                )

                self.assert_failed_without_state_change(
                    capability,
                    request,
                    error,
                )

    def test_path_escape_is_rejected_before_backend_execution(
        self,
    ) -> None:
        capability = self.make_capability(
            InjectedCalcFailureBackend(
                "backend_should_not_run"
            )
        )

        request = ActionRequest(
            "spreadsheet.inspect_workbook",
            {
                "path": (
                    "../../outside.xlsx"
                )
            },
        )

        self.assert_failed_without_state_change(
            capability,
            request,
            "invalid_path",
        )

    def test_absolute_path_is_rejected(
        self,
    ) -> None:
        capability = self.make_capability(
            InjectedCalcFailureBackend(
                "backend_should_not_run"
            )
        )

        request = ActionRequest(
            "spreadsheet.inspect_workbook",
            {
                "path": "/etc/passwd"
            },
        )

        self.assert_failed_without_state_change(
            capability,
            request,
            "invalid_path",
        )

    def test_unsupported_format_is_rejected(
        self,
    ) -> None:
        capability = self.make_capability(
            InjectedCalcFailureBackend(
                "backend_should_not_run"
            )
        )

        request = ActionRequest(
            "spreadsheet.inspect_workbook",
            {
                "path": "inputs/note.txt"
            },
        )

        self.assert_failed_without_state_change(
            capability,
            request,
            "unsupported_format",
        )

    def test_large_workbook_is_rejected(
        self,
    ) -> None:
        capability = self.make_capability(
            InjectedCalcFailureBackend(
                "backend_should_not_run"
            ),
            max_file_bytes=8,
        )

        request = ActionRequest(
            "spreadsheet.inspect_workbook",
            {
                "path": (
                    "inputs/invoice.xlsx"
                )
            },
        )

        self.assert_failed_without_state_change(
            capability,
            request,
            "file_too_large",
        )

    def test_invalid_read_range_arguments_are_rejected(
        self,
    ) -> None:
        capability = self.make_capability(
            InjectedCalcFailureBackend(
                "backend_should_not_run"
            )
        )

        cases = [
            (
                ActionRequest(
                    "spreadsheet.read_range",
                    {
                        "path": (
                            "inputs/invoice.xlsx"
                        ),
                        "sheet": "",
                        "range": "A1:B2",
                    },
                ),
                "invalid_sheet",
            ),
            (
                ActionRequest(
                    "spreadsheet.read_range",
                    {
                        "path": (
                            "inputs/invoice.xlsx"
                        ),
                        "sheet": "Fee Detail",
                        "range": "",
                    },
                ),
                "invalid_range",
            ),
        ]

        for request, error in cases:
            with self.subTest(error=error):
                self.assert_failed_without_state_change(
                    capability,
                    request,
                    error,
                )

    def test_invalid_find_arguments_are_rejected(
        self,
    ) -> None:
        capability = self.make_capability(
            InjectedCalcFailureBackend(
                "backend_should_not_run"
            )
        )

        cases = [
            (
                ActionRequest(
                    "spreadsheet.find",
                    {
                        "path": (
                            "inputs/invoice.xlsx"
                        ),
                        "query": "",
                    },
                ),
                "invalid_query",
            ),
            (
                ActionRequest(
                    "spreadsheet.find",
                    {
                        "path": (
                            "inputs/invoice.xlsx"
                        ),
                        "query": "x" * 33,
                    },
                ),
                "query_too_large",
            ),
            (
                ActionRequest(
                    "spreadsheet.find",
                    {
                        "path": (
                            "inputs/invoice.xlsx"
                        ),
                        "query": "research",
                        "max_results": 0,
                    },
                ),
                "invalid_result_limit",
            ),
            (
                ActionRequest(
                    "spreadsheet.find",
                    {
                        "path": (
                            "inputs/invoice.xlsx"
                        ),
                        "query": "research",
                        "case_sensitive": "yes",
                    },
                ),
                "invalid_query",
            ),
        ]

        for request, error in cases:
            with self.subTest(error=error):
                self.assert_failed_without_state_change(
                    capability,
                    request,
                    error,
                )


if __name__ == "__main__":
    unittest.main()