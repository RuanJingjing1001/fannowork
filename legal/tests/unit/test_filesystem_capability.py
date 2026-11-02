from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from fanno_work.models import ActionRequest

from fanno_legal.capabilities.filesystem import (
    FilesystemCapability,
)
from fanno_legal.state.audit import AuditLog
from fanno_legal.state.database import StateDatabase


FIXTURES = Path(__file__).parents[1] / "fixtures"


def load_seed(name: str) -> dict:
    return json.loads(
        (FIXTURES / name).read_text(
            encoding="utf-8"
        )
    )


class FilesystemCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = (
            tempfile.TemporaryDirectory()
        )
        self.root = Path(
            self.temporary_directory.name
        )
        self.workspace = self.root / "workspace"

        (self.workspace / "inputs").mkdir(
            parents=True
        )
        (self.workspace / "work").mkdir()
        (self.workspace / "outputs").mkdir()

        (
            self.workspace
            / "inputs"
            / "brief.txt"
        ).write_text(
            "Review the transaction risks.",
            encoding="utf-8",
        )

        self.database = StateDatabase(
            self.root / "state.db"
        )
        self.database.seed(
            load_seed("seed-a.json")
        )

        self.audit = AuditLog(
            self.root / "audit.jsonl"
        )

        self.capability = FilesystemCapability(
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
                "max_file_bytes": 1024,
            },
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_list_workspace_root(self) -> None:
        result = self.capability.act(
            "agent",
            ActionRequest(
                "filesystem.list",
                {"path": "."},
            ),
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            [
                item["path"]
                for item in result.output["entries"]
            ],
            [
                "inputs",
                "outputs",
                "work",
            ],
        )
        self.assertFalse(result.state_changed)

    def test_list_input_directory(self) -> None:
        result = self.capability.act(
            "agent",
            ActionRequest(
                "filesystem.list",
                {"path": "inputs"},
            ),
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            result.output["entries"][0]["path"],
            "inputs/brief.txt",
        )
        self.assertEqual(
            result.output["entries"][0]["kind"],
            "file",
        )

    def test_read_utf8_file(self) -> None:
        result = self.capability.act(
            "agent",
            ActionRequest(
                "filesystem.read",
                {
                    "path": "inputs/brief.txt"
                },
            ),
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            result.output["content"],
            "Review the transaction risks.",
        )
        self.assertEqual(
            result.output["size"],
            len(
                "Review the transaction risks."
                .encode("utf-8")
            ),
        )
        self.assertFalse(result.state_changed)
        self.assertEqual(
            self.database.get_state_version(),
            0,
        )

    def test_write_output_file(self) -> None:
        result = self.capability.act(
            "agent",
            ActionRequest(
                "filesystem.write",
                {
                    "path": "outputs/risk_note.md",
                    "content": "# Risk note\n",
                },
            ),
        )

        output_path = (
            self.workspace
            / "outputs"
            / "risk_note.md"
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.state_changed)
        self.assertTrue(output_path.is_file())
        self.assertEqual(
            output_path.read_text(
                encoding="utf-8"
            ),
            "# Risk note\n",
        )
        self.assertEqual(
            self.database.get_state_version(),
            1,
        )

    def test_write_input_is_denied(self) -> None:
        original = (
            self.workspace
            / "inputs"
            / "brief.txt"
        ).read_text(encoding="utf-8")

        result = self.capability.act(
            "agent",
            ActionRequest(
                "filesystem.write",
                {
                    "path": "inputs/brief.txt",
                    "content": "Changed",
                },
            ),
        )

        self.assertFalse(result.ok)
        self.assertEqual(
            result.error,
            "permission_denied",
        )
        self.assertEqual(
            (
                self.workspace
                / "inputs"
                / "brief.txt"
            ).read_text(encoding="utf-8"),
            original,
        )
        self.assertEqual(
            self.database.get_state_version(),
            0,
        )

    def test_path_escape_is_rejected(self) -> None:
        result = self.capability.act(
            "agent",
            ActionRequest(
                "filesystem.write",
                {
                    "path": "../escape.txt",
                    "content": "escape",
                },
            ),
        )

        self.assertFalse(result.ok)
        self.assertEqual(
            result.error,
            "invalid_path",
        )
        self.assertFalse(
            (self.root / "escape.txt").exists()
        )
        self.assertEqual(
            self.database.get_state_version(),
            0,
        )

    def test_large_file_is_rejected(self) -> None:
        small_limit_capability = (
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
                    "max_file_bytes": 4,
                },
            )
        )

        result = small_limit_capability.act(
            "agent",
            ActionRequest(
                "filesystem.write",
                {
                    "path": "outputs/large.txt",
                    "content": "12345",
                },
            ),
        )

        self.assertFalse(result.ok)
        self.assertEqual(
            result.error,
            "file_too_large",
        )
        self.assertFalse(
            (
                self.workspace
                / "outputs"
                / "large.txt"
            ).exists()
        )

    def test_same_content_does_not_advance_version(self) -> None:
        request = ActionRequest(
            "filesystem.write",
            {
                "path": "outputs/result.txt",
                "content": "same content",
            },
        )

        first = self.capability.act(
            "agent",
            request,
        )
        second = self.capability.act(
            "agent",
            request,
        )

        self.assertTrue(first.state_changed)
        self.assertFalse(second.state_changed)
        self.assertEqual(
            self.database.get_state_version(),
            1,
        )

        events = self.audit.read_all()

        self.assertTrue(
            events[0]["state_changed"]
        )
        self.assertFalse(
            events[1]["state_changed"]
        )

    def test_write_audit_does_not_store_content(self) -> None:
        self.capability.act(
            "agent",
            ActionRequest(
                "filesystem.write",
                {
                    "path": "outputs/private.txt",
                    "content": "confidential text",
                },
            ),
        )

        event = self.audit.read_all()[0]
        arguments = event["arguments"]

        self.assertNotIn(
            "content",
            arguments,
        )
        self.assertEqual(
            arguments["path"],
            "outputs/private.txt",
        )
        self.assertEqual(
            arguments["content_bytes"],
            len(
                "confidential text".encode(
                    "utf-8"
                )
            ),
        )
        self.assertIn(
            "content_sha256",
            arguments,
        )

    def test_denied_write_is_audited(self) -> None:
        self.capability.act(
            "agent",
            ActionRequest(
                "filesystem.write",
                {
                    "path": "inputs/brief.txt",
                    "content": "forbidden",
                },
            ),
        )

        event = self.audit.read_all()[0]

        self.assertFalse(event["ok"])
        self.assertEqual(
            event["error"],
            "permission_denied",
        )
        self.assertFalse(
            event["state_changed"]
        )


if __name__ == "__main__":
    unittest.main()
