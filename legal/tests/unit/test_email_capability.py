from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from typing import Any

from fanno_work.models import ActionRequest

from fanno_legal.backends.email import (
    EmailBackendFailure,
)
from fanno_legal.capabilities.email import (
    EmailCapability,
)
from fanno_legal.capabilities.filesystem import (
    FilesystemCapability,
)
from fanno_legal.state.audit import AuditLog
from fanno_legal.state.database import (
    StateDatabase,
)


FIXTURES = Path(__file__).parents[1] / "fixtures"


class FakeEmailBackend:
    backend_id = "fake-email"

    def __init__(
        self,
        workspace: Path,
        content: bytes,
    ) -> None:
        self.workspace = workspace
        self.content = content
        self.download_calls = 0

    def search(self, **_: Any) -> dict[str, Any]:
        return {"messages": []}

    def read(self, **_: Any) -> dict[str, Any]:
        return {}

    def send(self, **_: Any) -> dict[str, Any]:
        return {}

    def download_attachment(
        self,
        *,
        message_id: str,
        attachment_id: str,
        target_path: str,
        max_bytes: int,
    ) -> dict[str, Any]:
        self.download_calls += 1
        if len(self.content) > max_bytes:
            raise EmailBackendFailure(
                "attachment_too_large"
            )

        path = self.workspace / target_path
        existed = path.exists()
        changed = (
            not existed
            or path.read_bytes() != self.content
        )
        if changed:
            path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            path.write_bytes(self.content)

        return {
            "message_id": message_id,
            "attachment_id": attachment_id,
            "path": target_path,
            "filename": "invoice.txt",
            "content_type": "text/plain",
            "size": len(self.content),
            "sha256": hashlib.sha256(
                self.content
            ).hexdigest(),
            "created": not existed,
            "changed": changed,
            "backend": self.backend_id,
            "execution_evidence": {
                "runtime": "python",
                "execution_scope": "test",
            },
        }


class EmailCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = (
            tempfile.TemporaryDirectory()
        )
        self.root = Path(
            self.temporary_directory.name
        )
        self.workspace = self.root / "workspace"
        for name in ("inputs", "work", "outputs"):
            (self.workspace / name).mkdir(
                parents=True,
                exist_ok=True,
            )

        seed = json.loads(
            (FIXTURES / "seed-a.json").read_text(
                encoding="utf-8"
            )
        )
        self.database = StateDatabase(
            self.root / "state.db"
        )
        self.database.seed(seed)
        self.audit = AuditLog(
            self.root / "audit.jsonl"
        )
        self.filesystem = FilesystemCapability(
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
        self.backend = FakeEmailBackend(
            self.workspace,
            b"invoice attachment\n",
        )
        self.capability = EmailCapability(
            backend=self.backend,
            filesystem=self.filesystem,
            database=self.database,
            audit=self.audit,
            config={
                "max_results": 100,
                "max_query_characters": 500,
                "max_subject_characters": 500,
                "max_body_characters": 100000,
                "max_attachment_bytes": 1024,
                "attachment_writable_prefixes": [
                    "work/email-attachments/"
                ],
                "allowed_recipient_domains": [
                    "fanno.test"
                ],
            },
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_download_attachment_writes_file(self) -> None:
        result = self.capability.act(
            "agent",
            ActionRequest(
                "email.download_attachment",
                {
                    "message_id": "message-1",
                    "attachment_id": "part-2",
                    "target_path": (
                        "work/email-attachments/"
                        "invoice.txt"
                    ),
                },
            ),
        )

        path = (
            self.workspace
            / "work"
            / "email-attachments"
            / "invoice.txt"
        )
        self.assertTrue(result.ok, result.error)
        self.assertTrue(result.state_changed)
        self.assertEqual(
            path.read_bytes(),
            b"invoice attachment\n",
        )
        self.assertEqual(
            result.output["sha256"],
            hashlib.sha256(
                b"invoice attachment\n"
            ).hexdigest(),
        )
        self.assertEqual(
            self.database.get_state_version(),
            1,
        )

    def test_same_attachment_is_idempotent(self) -> None:
        request = ActionRequest(
            "email.download_attachment",
            {
                "message_id": "message-1",
                "attachment_id": "part-2",
                "target_path": (
                    "work/email-attachments/"
                    "invoice.txt"
                ),
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

    def test_attachment_cannot_write_to_inputs(self) -> None:
        result = self.capability.act(
            "agent",
            ActionRequest(
                "email.download_attachment",
                {
                    "message_id": "message-1",
                    "attachment_id": "part-2",
                    "target_path": (
                        "inputs/invoice.txt"
                    ),
                },
            ),
        )

        self.assertFalse(result.ok)
        self.assertEqual(
            result.error,
            "permission_denied",
        )
        self.assertEqual(
            self.backend.download_calls,
            0,
        )
        self.assertEqual(
            self.database.get_state_version(),
            0,
        )

    def test_attachment_path_escape_is_rejected(self) -> None:
        result = self.capability.act(
            "agent",
            ActionRequest(
                "email.download_attachment",
                {
                    "message_id": "message-1",
                    "attachment_id": "part-2",
                    "target_path": "../invoice.txt",
                },
            ),
        )

        self.assertFalse(result.ok)
        self.assertEqual(
            result.error,
            "invalid_path",
        )
        self.assertEqual(
            self.backend.download_calls,
            0,
        )


if __name__ == "__main__":
    unittest.main()
