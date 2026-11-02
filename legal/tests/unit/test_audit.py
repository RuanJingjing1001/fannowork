from pathlib import Path
import tempfile
import unittest

from fanno_legal.state.audit import AuditLog


class AuditLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = (
            tempfile.TemporaryDirectory()
        )
        self.path = (
            Path(self.temporary_directory.name)
            / "audit.jsonl"
        )
        self.audit = AuditLog(self.path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_empty_log(self) -> None:
        self.assertEqual(
            self.audit.read_all(),
            [],
        )

    def test_append_action_event(self) -> None:
        event = self.audit.append(
            actor="agent",
            action="records.get",
            arguments={
                "collection": "transactions",
                "record_id": "TX-TEST-001",
            },
            ok=True,
            error=None,
            state_version_before=0,
            state_version_after=0,
        )

        self.assertEqual(event["sequence"], 1)
        self.assertFalse(event["state_changed"])
        self.assertEqual(
            self.audit.read_all(),
            [event],
        )

    def test_state_change_is_recorded(self) -> None:
        event = self.audit.append(
            actor="agent",
            action="records.update",
            arguments={
                "collection": "transactions",
                "record_id": "TX-TEST-001",
                "fields": {"status": "reviewed"},
            },
            ok=True,
            error=None,
            state_version_before=0,
            state_version_after=1,
        )

        self.assertTrue(event["state_changed"])

    def test_denied_action_is_recorded(self) -> None:
        event = self.audit.append(
            actor="agent",
            action="records.update",
            arguments={
                "collection": "transactions",
                "record_id": "TX-TEST-001",
                "fields": {"agreed_price": 1},
            },
            ok=False,
            error="permission_denied",
            state_version_before=0,
            state_version_after=0,
        )

        self.assertFalse(event["ok"])
        self.assertFalse(event["state_changed"])

    def test_second_client_can_read_log(self) -> None:
        self.audit.append(
            actor="agent",
            action="records.get",
            arguments={},
            ok=True,
            error=None,
            state_version_before=0,
            state_version_after=0,
        )

        independent_client = AuditLog(self.path)
        events = independent_client.read_all()

        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0]["event_id"],
            "audit-000001",
        )


if __name__ == "__main__":
    unittest.main()
