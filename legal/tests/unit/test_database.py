import json
from pathlib import Path
import tempfile
import unittest

from fanno_legal.state.database import StateDatabase


FIXTURES = Path(__file__).parents[1] / "fixtures"


def load_seed(name: str) -> dict:
    return json.loads(
        (FIXTURES / name).read_text(
            encoding="utf-8"
        )
    )


class StateDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database = StateDatabase(
            Path(self.temp_directory.name) / "state.db"
        )

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_seed_and_read_record(self) -> None:
        self.database.seed(
            load_seed("seed-a.json")
        )

        record = self.database.get_record(
            "transactions",
            "TX-TEST-001",
        )

        self.assertIsNotNone(record)
        self.assertEqual(
            record["fields"]["client"],
            "Client A",
        )
        self.assertEqual(
            self.database.get_state_version(),
            0,
        )

    def test_update_changes_record_and_version(self) -> None:
        self.database.seed(
            load_seed("seed-a.json")
        )

        updated = self.database.update_record(
            "transactions",
            "TX-TEST-001",
            {
                "risk_notes": "Pending title review"
            },
        )

        self.assertEqual(
            updated["fields"]["risk_notes"],
            "Pending title review",
        )
        self.assertEqual(updated["version"], 2)
        self.assertEqual(
            self.database.get_state_version(),
            1,
        )

    def test_reset_restores_seed(self) -> None:
        self.database.seed(
            load_seed("seed-a.json")
        )
        initial = self.database.business_snapshot()

        self.database.update_record(
            "transactions",
            "TX-TEST-001",
            {"risk_notes": "Changed"},
        )
        self.database.reset()

        restored = self.database.business_snapshot()

        self.assertEqual(restored, initial)

    def test_different_seed_works(self) -> None:
        self.database.seed(
            load_seed("seed-b.json")
        )

        record = self.database.get_record(
            "matters",
            "MATTER-002",
        )

        self.assertIsNotNone(record)
        self.assertEqual(
            record["fields"]["matter_type"],
            "employment",
        )
    def test_seed_stores_policy(self) -> None:
        self.database.seed(
            load_seed("seed-a.json")
            )

        policy = self.database.get_policy(
        actor="agent",
        collection="transactions",
        operation="update",
    )

        self.assertIsNotNone(policy)
        self.assertEqual(
            policy["allowed_fields"],
            ["status", "risk_notes"],
        )
    def test_missing_policy_returns_none(self) -> None:
        self.database.seed(
            load_seed("seed-a.json")
    )

        policy = self.database.get_policy(
            actor="agent",
            collection="transactions",
            operation="create",
        )

        self.assertIsNone(policy)

    def test_seed_b_has_different_policy(self) -> None:
        self.database.seed(
            load_seed("seed-b.json")
        )

        policy = self.database.get_policy(
        actor="agent",
        collection="matters",
        operation="update",
    )

        self.assertEqual(
            policy["allowed_fields"],
            ["status", "internal_notes"],
        )

    def test_advance_state_version(self) -> None:
        self.database.seed(
            load_seed("seed-a.json")
        )

        result = self.database.advance_state_version()

        self.assertEqual(result, 1)
        self.assertEqual(
            self.database.get_state_version(),
            1,
        )
