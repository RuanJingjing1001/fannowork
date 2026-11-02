from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from fanno_work.models import (
    ActionRequest,
)

from fanno_legal.backends.document.libreoffice_uno import (
    LibreOfficeEdit,
    LibreOfficeUnoBackend,
    LibreOfficeUnoFailure,
)
from fanno_legal.capabilities.filesystem import (
    FilesystemCapability,
)
from fanno_legal.capabilities.writer import (
    WriterCapability,
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


class InjectedWriterFailureBackend(
    LibreOfficeUnoBackend
):
    def __init__(
        self,
        code: str,
    ) -> None:
        self.code = code

    def _fail(self):
        raise LibreOfficeUnoFailure(
            self.code
        )

    def create_document(
        self,
        **kwargs,
    ) -> LibreOfficeEdit:
        del kwargs
        return self._fail()

    def insert_heading(
        self,
        **kwargs,
    ) -> LibreOfficeEdit:
        del kwargs
        return self._fail()

    def insert_paragraph(
        self,
        **kwargs,
    ) -> LibreOfficeEdit:
        del kwargs
        return self._fail()

    def add_table(
        self,
        **kwargs,
    ) -> LibreOfficeEdit:
        del kwargs
        return self._fail()

    def save_as(
        self,
        **kwargs,
    ) -> LibreOfficeEdit:
        del kwargs
        return self._fail()


class MismatchedWriterBackend(
    LibreOfficeUnoBackend
):
    def __init__(self) -> None:
        pass

    def add_table(
        self,
        *,
        source: Path,
        target: Path,
        headers: list[str],
        rows: list[list[str]],
    ) -> LibreOfficeEdit:
        del source, headers, rows

        unexpected = (
            target.parent
            / "unexpected-output.odt"
        )
        unexpected.write_bytes(
            b"unexpected writer output"
        )

        content = unexpected.read_bytes()

        return LibreOfficeEdit(
            generated_path=unexpected,
            backend=(
                "injected-mismatched-writer"
            ),
            backend_version="test-1",
            size=len(content),
            sha256=hashlib.sha256(
                content
            ).hexdigest(),
            stdout="",
            stderr="",
            execution_evidence={
                "execution_scope": "test",
            },
        )


class WriterFailureSafetyTests(
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

        self.protected_input = (
            self.workspace
            / "inputs"
            / "protected.odt"
        )
        self.protected_input.write_bytes(
            b"protected input document"
        )

        self.draft = (
            self.workspace
            / "work"
            / "draft.odt"
        )
        self.draft.write_bytes(
            b"original draft document"
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
        backend: LibreOfficeUnoBackend,
    ) -> WriterCapability:
        return WriterCapability(
            filesystem=self.filesystem,
            database=self.database,
            audit=self.audit,
            backend=backend,
            config={
                "max_file_bytes": (
                    1024 * 1024
                ),
                "uno": {
                    "system_python": (
                        "/usr/bin/python3"
                    ),
                    "soffice_executable": (
                        "soffice"
                    ),
                    "timeout_seconds": 60,
                    "max_insert_characters": (
                        20_000
                    ),
                    "max_output_bytes": (
                        10 * 1024 * 1024
                    ),
                    "max_table_rows": 200,
                    "max_table_columns": 20,
                    "input_extensions": [
                        ".odt",
                        ".docx",
                    ],
                    "output_extensions": [
                        ".odt",
                        ".docx",
                    ],
                },
            },
        )

    def snapshot(self) -> dict:
        return build_environment_snapshot(
            self.database,
            self.workspace,
        )

    def writer_requests(
        self,
    ) -> list[ActionRequest]:
        return [
            ActionRequest(
                "document.create",
                {
                    "path": (
                        "outputs/new-document.odt"
                    ),
                    "title": "Test Document",
                },
            ),
            ActionRequest(
                "document.insert_heading",
                {
                    "path": "work/draft.odt",
                    "text": "Executive Summary",
                    "level": 1,
                },
            ),
            ActionRequest(
                "document.insert_paragraph",
                {
                    "path": "work/draft.odt",
                    "text": (
                        "Review remains required."
                    ),
                    "style": "Body Text",
                },
            ),
            ActionRequest(
                "document.add_table",
                {
                    "path": "work/draft.odt",
                    "headers": [
                        "Issue",
                        "Adjustment",
                    ],
                    "rows": [
                        [
                            "Research charge",
                            "$4,850.00",
                        ]
                    ],
                },
            ),
            ActionRequest(
                "document.save_as",
                {
                    "source_path": (
                        "work/draft.odt"
                    ),
                    "target_path": (
                        "outputs/draft.docx"
                    ),
                },
            ),
        ]

    def protected_requests(
        self,
    ) -> list[ActionRequest]:
        return [
            ActionRequest(
                "document.create",
                {
                    "path": (
                        "inputs/new-document.odt"
                    ),
                    "title": "Forbidden",
                },
            ),
            ActionRequest(
                "document.insert_heading",
                {
                    "path": (
                        "inputs/protected.odt"
                    ),
                    "text": "Forbidden",
                    "level": 1,
                },
            ),
            ActionRequest(
                "document.insert_paragraph",
                {
                    "path": (
                        "inputs/protected.odt"
                    ),
                    "text": "Forbidden",
                },
            ),
            ActionRequest(
                "document.add_table",
                {
                    "path": (
                        "inputs/protected.odt"
                    ),
                    "headers": ["Issue"],
                    "rows": [["Forbidden"]],
                },
            ),
            ActionRequest(
                "document.save_as",
                {
                    "source_path": (
                        "inputs/protected.odt"
                    ),
                    "target_path": (
                        "inputs/copied.docx"
                    ),
                },
            ),
        ]

    def assert_failed_without_state_change(
        self,
        capability: WriterCapability,
        request: ActionRequest,
        expected_error: str,
    ) -> None:
        before = self.snapshot()
        version_before = (
            self.database.get_state_version()
        )
        draft_before = (
            self.draft.read_bytes()
        )
        input_before = (
            self.protected_input.read_bytes()
        )

        result = capability.act(
            "agent",
            request,
        )

        after = self.snapshot()

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
            after["digest"],
            before["digest"],
        )
        self.assertEqual(
            self.draft.read_bytes(),
            draft_before,
        )
        self.assertEqual(
            self.protected_input.read_bytes(),
            input_before,
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

    def test_timeout_is_atomic_for_every_writer_action(
        self,
    ) -> None:
        capability = self.make_capability(
            InjectedWriterFailureBackend(
                "uno_timeout"
            )
        )

        for request in self.writer_requests():
            with self.subTest(
                action=request.name
            ):
                self.assert_failed_without_state_change(
                    capability,
                    request,
                    "uno_timeout",
                )

    def test_representative_backend_errors_are_preserved(
        self,
    ) -> None:
        for error in (
            "uno_connection_failed",
            "document_open_failed",
            "document_save_failed",
        ):
            with self.subTest(error=error):
                capability = self.make_capability(
                    InjectedWriterFailureBackend(
                        error
                    )
                )

                request = ActionRequest(
                    "document.create",
                    {
                        "path": (
                            "outputs/"
                            f"{error}.odt"
                        ),
                        "title": "Failure Test",
                    },
                )

                self.assert_failed_without_state_change(
                    capability,
                    request,
                    error,
                )

    def test_mismatched_output_is_rejected_atomically(
        self,
    ) -> None:
        capability = self.make_capability(
            MismatchedWriterBackend()
        )

        request = ActionRequest(
            "document.add_table",
            {
                "path": "work/draft.odt",
                "headers": [
                    "Issue",
                    "Adjustment",
                ],
                "rows": [
                    [
                        "Research charge",
                        "$4,850.00",
                    ]
                ],
            },
        )

        self.assert_failed_without_state_change(
            capability,
            request,
            "edit_output_mismatch",
        )

    def test_input_directory_is_protected_for_every_writer_action(
        self,
    ) -> None:
        capability = self.make_capability(
            InjectedWriterFailureBackend(
                "backend_should_not_run"
            )
        )

        for request in self.protected_requests():
            with self.subTest(
                action=request.name
            ):
                self.assert_failed_without_state_change(
                    capability,
                    request,
                    "permission_denied",
                )

    def test_failure_leaves_no_writer_temporary_directories(
        self,
    ) -> None:
        capability = self.make_capability(
            InjectedWriterFailureBackend(
                "document_save_failed"
            )
        )

        request = ActionRequest(
            "document.insert_paragraph",
            {
                "path": "work/draft.odt",
                "text": "This must fail.",
            },
        )

        self.assert_failed_without_state_change(
            capability,
            request,
            "document_save_failed",
        )

        temporary_directories = [
            path
            for path in (
                self.workspace / "work"
            ).iterdir()
            if path.name.startswith(
                ".writer-uno-"
            )
        ]

        self.assertEqual(
            temporary_directories,
            [],
        )


if __name__ == "__main__":
    unittest.main()