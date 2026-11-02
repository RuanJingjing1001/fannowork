from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from fanno_work.evaluation import (
    EvaluationContext,
    RewardEngine,
    parse_evaluator_spec,
)


def create_docx(
    path: Path,
    text: str,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document '
        'xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>"
        f"{text}"
        "</w:t></w:r></w:p>"
        "</w:body>"
        "</w:document>"
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/'
        'package/2006/content-types">'
        '<Default Extension="xml" '
        'ContentType="application/xml"/>'
        "</Types>"
    )

    with zipfile.ZipFile(
        path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr(
            "[Content_Types].xml",
            content_types,
        )
        archive.writestr(
            "word/document.xml",
            document_xml,
        )


def evaluator_spec():
    return parse_evaluator_spec(
        {
            "schema_version": 1,
            "id": "test.evaluator.v1",
            "version": "1.0.0",
            "adapter": "reward_engine",
            "status": "test",
            "policy": {
                "aggregation": (
                    "weighted_mean"
                ),
                "pass_threshold": 0.75,
                "hard_failure_reward": 0.0,
            },
            "checks": [
                {
                    "id": "artifact_exists",
                    "type": (
                        "required_artifacts"
                    ),
                    "component": "artifact",
                    "weight": 0.2,
                    "hard": True,
                    "config": {
                        "paths": [
                            "memo.docx"
                        ]
                    },
                },
                {
                    "id": "docx_valid",
                    "type": "docx_openable",
                    "component": "artifact",
                    "weight": 0.2,
                    "hard": True,
                    "config": {
                        "path": "memo.docx"
                    },
                },
                {
                    "id": "inputs_unchanged",
                    "type": (
                        "input_hashes_unchanged"
                    ),
                    "component": "safety",
                    "weight": 0.0,
                    "hard": True,
                    "config": {},
                },
                {
                    "id": "memo_content",
                    "type": (
                        "document_contains"
                    ),
                    "component": (
                        "business_result"
                    ),
                    "weight": 0.6,
                    "hard": False,
                    "config": {
                        "path": "memo.docx",
                        "min_characters": 20,
                        "all_terms": [
                            "research",
                            "adjustment"
                        ],
                        "any_terms": [
                            "invoice",
                            "guidelines"
                        ],
                    },
                },
            ],
        }
    )


class RewardEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = (
            tempfile.TemporaryDirectory()
        )
        self.root = Path(
            self.temporary_directory.name
        )
        self.bundle_root = (
            self.root / "bundle"
        )
        self.run_dir = self.root / "run"

        (
            self.bundle_root / "assets"
        ).mkdir(
            parents=True,
            exist_ok=True,
        )
        (
            self.run_dir
            / "workspace"
            / "inputs"
        ).mkdir(
            parents=True,
            exist_ok=True,
        )
        (
            self.run_dir / "artifacts"
        ).mkdir(
            parents=True,
            exist_ok=True,
        )

        self.source_input = (
            self.bundle_root
            / "assets"
            / "invoice.txt"
        )
        self.workspace_input = (
            self.run_dir
            / "workspace"
            / "inputs"
            / "invoice.txt"
        )

        self.source_input.write_text(
            "invoice source\n",
            encoding="utf-8",
        )
        self.workspace_input.write_text(
            "invoice source\n",
            encoding="utf-8",
        )

        self.task = {
            "id": "test.reward.v1",
            "inputs": [
                {
                    "path": "invoice.txt",
                    "source": (
                        "assets/invoice.txt"
                    ),
                }
            ],
        }

        self.context = EvaluationContext(
            task_id="test.reward.v1",
            task=self.task,
            bundle_root=self.bundle_root,
            run_dir=self.run_dir,
            initial_snapshot={
                "digest": "initial"
            },
            final_snapshot={
                "digest": "final"
            },
            reset_snapshot={
                "digest": "initial"
            },
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_valid_run_receives_full_reward(
        self,
    ) -> None:
        create_docx(
            self.run_dir
            / "artifacts"
            / "memo.docx",
            (
                "Research invoice review and "
                "recommended adjustment under "
                "the billing guidelines."
            ),
        )

        result = RewardEngine().evaluate(
            self.context,
            evaluator_spec(),
        )

        self.assertTrue(result.passed)
        self.assertTrue(
            result.hard_constraints_passed
        )
        self.assertEqual(
            result.reward,
            1.0,
        )
        self.assertTrue(
            (
                self.run_dir
                / "evaluation"
                / "result.json"
            ).is_file()
        )
        self.assertTrue(
            (
                self.run_dir
                / "evaluation"
                / "evidence.json"
            ).is_file()
        )

    def test_hard_failure_forces_zero_reward(
        self,
    ) -> None:
        result = RewardEngine().evaluate(
            self.context,
            evaluator_spec(),
        )

        self.assertFalse(result.passed)
        self.assertFalse(
            result.hard_constraints_passed
        )
        self.assertEqual(
            result.reward,
            0.0,
        )

    def test_modified_input_is_hard_failure(
        self,
    ) -> None:
        create_docx(
            self.run_dir
            / "artifacts"
            / "memo.docx",
            (
                "Research invoice review and "
                "recommended adjustment under "
                "the billing guidelines."
            ),
        )

        self.workspace_input.write_text(
            "tampered\n",
            encoding="utf-8",
        )

        result = RewardEngine().evaluate(
            self.context,
            evaluator_spec(),
        )

        self.assertFalse(result.passed)
        self.assertEqual(
            result.reward,
            0.0,
        )

        input_check = next(
            check
            for check in result.checks
            if check.id == "inputs_unchanged"
        )

        self.assertFalse(
            input_check.passed
        )
        self.assertEqual(
            input_check.evidence[
                "changed_or_missing"
            ],
            ["invoice.txt"],
        )

    def test_partial_content_gets_partial_score(
        self,
    ) -> None:
        create_docx(
            self.run_dir
            / "artifacts"
            / "memo.docx",
            (
                "Research review of the invoice "
                "without a recommendation."
            ),
        )

        result = RewardEngine().evaluate(
            self.context,
            evaluator_spec(),
        )

        self.assertTrue(
            result.hard_constraints_passed
        )
        self.assertGreater(
            result.reward,
            0.0,
        )
        self.assertLess(
            result.reward,
            1.0,
        )

    def test_result_json_matches_result(
        self,
    ) -> None:
        create_docx(
            self.run_dir
            / "artifacts"
            / "memo.docx",
            (
                "Research invoice review and "
                "recommended adjustment under "
                "the billing guidelines."
            ),
        )

        result = RewardEngine().evaluate(
            self.context,
            evaluator_spec(),
        )

        stored = json.loads(
            (
                self.run_dir
                / "evaluation"
                / "result.json"
            ).read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            stored["reward"],
            result.reward,
        )
        self.assertEqual(
            stored["task_id"],
            "test.reward.v1",
        )


if __name__ == "__main__":
    unittest.main()
