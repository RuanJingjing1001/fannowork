from __future__ import annotations

import json
from pathlib import Path
import unittest

from fanno_work.specs import (
    load_environment,
)

from fanno_legal.contracts import (
    load_action_contracts,
)


LEGAL_ROOT = Path(__file__).parents[2]
ENVIRONMENT_PATH = (
    LEGAL_ROOT
    / "environments"
    / "legal-office"
    / "v1"
    / "environment.json"
)
MATRIX_PATH = (
    LEGAL_ROOT
    / "docs"
    / "action-verification-matrix.json"
)


class ActionVerificationMatrixTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.matrix = json.loads(
            MATRIX_PATH.read_text(
                encoding="utf-8"
            )
        )
        environment = load_environment(
            ENVIRONMENT_PATH
        )
        self.contracts = {
            contract.id: contract
            for contract in load_action_contracts(
                environment
            )
        }

    def test_matrix_covers_every_declared_action(
        self,
    ) -> None:
        entries = self.matrix["actions"]
        ids = [
            entry["id"]
            for entry in entries
        ]

        self.assertEqual(
            len(ids),
            len(set(ids)),
        )
        self.assertEqual(
            set(ids),
            set(self.contracts),
        )

    def test_effects_match_action_contracts(
        self,
    ) -> None:
        for entry in self.matrix["actions"]:
            with self.subTest(
                action=entry["id"]
            ):
                self.assertEqual(
                    entry["effect"],
                    self.contracts[
                        entry["id"]
                    ].effect,
                )

    def test_levels_and_evidence_are_valid(
        self,
    ) -> None:
        levels = set(
            self.matrix["levels"]
        )
        dimensions = (
            "contract",
            "backend",
            "observation",
            "independent_check",
            "permission",
            "failure",
            "reset",
        )

        for entry in self.matrix["actions"]:
            with self.subTest(
                action=entry["id"]
            ):
                for dimension in dimensions:
                    self.assertIn(
                        entry[dimension],
                        levels,
                    )

                evidence = entry["evidence"]
                self.assertTrue(evidence)

                for relative_path in evidence:
                    path = (
                        LEGAL_ROOT.parent
                        / relative_path
                    )
                    self.assertTrue(
                        path.is_file(),
                        relative_path,
                    )


if __name__ == "__main__":
    unittest.main()
