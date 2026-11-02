import json
from pathlib import Path
import tempfile
import unittest

from fanno_work.models import ActionRequest

from fanno_legal.capabilities.records import RecordsCapability
from fanno_legal.state.audit import AuditLog
from fanno_legal.state.database import StateDatabase


FIXTURES = Path(__file__).parents[1] / "fixtures"


class RecordsCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = StateDatabase(
            Path(self.temporary_directory.name) / "state.db"
        )
        self.audit = AuditLog(
            Path(self.temporary_directory.name) / "audit.jsonl"
        )
        seed = json.loads(
            (FIXTURES / "seed-a.json").read_text("utf-8")
        )
        self.database.seed(seed)
        self.capability = RecordsCapability(
            self.database,
            self.audit,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_agent_can_get_record(self) -> None:
        result = self.capability.act(
            "agent",
            ActionRequest(
                "records.get",
                {
                    "collection": "transactions",
                    "record_id": "TX-TEST-001",
                },
            ),
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            result.output["record"]["fields"]["client"],
            "Client A",
        )
        self.assertFalse(result.state_changed)

    def test_agent_can_update_allowed_field(self) -> None:
        result = self.capability.act(
            "agent",
            ActionRequest(
                "records.update",
                {
                    "collection": "transactions",
                    "record_id": "TX-TEST-001",
                    "fields": {
                        "risk_notes": "Title review required"
                    },
                },
            ),
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.state_changed)

    def test_agent_can_query_records_without_changing_state(
        self,
    ) -> None:
        version_before = (
            self.database.get_state_version()
        )

        result = self.capability.act(
            "agent",
            ActionRequest(
                "records.query",
                {
                    "collection": "transactions",
                    "filters": {
                        "status": "under_review"
                    },
                },
            ),
        )

        independently_read = (
            self.database.list_records(
                "transactions"
            )
        )

        self.assertTrue(result.ok)
        self.assertFalse(
            result.state_changed
        )
        self.assertEqual(
            self.database.get_state_version(),
            version_before,
        )
        self.assertEqual(
            result.output["records"],
            independently_read,
        )

        event = self.audit.read_all()[-1]
        self.assertEqual(
            event["action"],
            "records.query",
        )
        self.assertTrue(event["ok"])
        self.assertFalse(
            event["state_changed"]
        )

    def test_query_rejects_invalid_filter(
        self,
    ) -> None:
        version_before = (
            self.database.get_state_version()
        )

        result = self.capability.act(
            "agent",
            ActionRequest(
                "records.query",
                {
                    "collection": "transactions",
                    "filters": [],
                },
            ),
        )

        self.assertFalse(result.ok)
        self.assertEqual(
            result.error,
            "invalid_filter",
        )
        self.assertEqual(
            self.database.get_state_version(),
            version_before,
        )

    def test_agent_cannot_update_forbidden_field(self) -> None:
        version_before = self.database.get_state_version()

        result = self.capability.act(
            "agent",
            ActionRequest(
                "records.update",
                {
                    "collection": "transactions",
                    "record_id": "TX-TEST-001",
                    "fields": {"agreed_price": 500000},
                },
            ),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "permission_denied")
        self.assertEqual(
            self.database.get_state_version(),
            version_before,
        )

    def test_unknown_actor_is_denied(self) -> None:
        result = self.capability.act(
            "unknown-user",
            ActionRequest(
                "records.get",
                {
                    "collection": "transactions",
                    "record_id": "TX-TEST-001",
                },
            ),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "permission_denied")

    def test_read_action_is_audited(self) -> None:
        self.capability.act(
            "agent",
            ActionRequest(
                "records.get",
                {
                    "collection": "transactions",
                    "record_id": "TX-TEST-001",
                },
            ),
        )

        events = self.audit.read_all()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "records.get")
        self.assertTrue(events[0]["ok"])
        self.assertFalse(events[0]["state_changed"])

    def test_update_records_version_change(self) -> None:
        self.capability.act(
            "agent",
            ActionRequest(
                "records.update",
                {
                    "collection": "transactions",
                    "record_id": "TX-TEST-001",
                    "fields": {"status": "reviewed"},
                },
            ),
        )

        event = self.audit.read_all()[0]

        self.assertEqual(event["state_version_before"], 0)
        self.assertEqual(event["state_version_after"], 1)
        self.assertTrue(event["state_changed"])

    def test_denied_action_is_audited(self) -> None:
        self.capability.act(
            "agent",
            ActionRequest(
                "records.update",
                {
                    "collection": "transactions",
                    "record_id": "TX-TEST-001",
                    "fields": {"agreed_price": 1},
                },
            ),
        )

        event = self.audit.read_all()[0]

        self.assertFalse(event["ok"])
        self.assertEqual(event["error"], "permission_denied")
        self.assertEqual(
            event["state_version_before"],
            event["state_version_after"],
        )


if __name__ == "__main__":
    unittest.main()
