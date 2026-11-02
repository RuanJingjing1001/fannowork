from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from fanno_work.tasking import (
    TaskBundleResolver,
    validate_task_bundle,
)


LEGAL_ROOT = Path(__file__).parents[2]

TASK_PATH = (
    LEGAL_ROOT
    / "tasks"
    / "imported"
    / "harvey-lab"
    / "litigation-invoice-review"
    / "v1"
    / "task.json"
)

ENVIRONMENT_PATH = (
    LEGAL_ROOT
    / "environments"
    / "legal-office"
    / "v1"
    / "environment.container.json"
)


class CanonicalLegalTaskBundleTests(
    unittest.TestCase
):
    def test_litigation_invoice_bundle_is_complete(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "canonical"

            bundle = TaskBundleResolver().build(
                task_path=TASK_PATH,
                environment_path=(
                    ENVIRONMENT_PATH
                ),
                output_dir=output,
            )

            manifest = validate_task_bundle(
                output
            )

            self.assertEqual(
                manifest.bundle_id,
                (
                    "legal.harvey-lab."
                    "litigation-invoice-review.v1"
                ),
            )
            self.assertEqual(
                manifest.environment["version"],
                "0.7.0",
            )
            self.assertEqual(
                manifest.action_catalog[
                    "action_count"
                ],
                26,
            )

            asset_paths = {
                item.path
                for item in manifest.files
                if item.role == "task_asset"
            }

            self.assertEqual(
                asset_paths,
                {
                    (
                        "assets/"
                        "doe-matter-july-2024-"
                        "summary.docx"
                    ),
                    (
                        "assets/"
                        "engagement-letter-cascade."
                        "docx"
                    ),
                    (
                        "assets/"
                        "hl-july-2024-invoice.xlsx"
                    ),
                    (
                        "assets/"
                        "hl-june-2024-invoice-"
                        "summary.docx"
                    ),
                    (
                        "assets/"
                        "holt-email-research-"
                        "concerns.eml"
                    ),
                    (
                        "assets/"
                        "terraverde-billing-"
                        "guidelines.docx"
                    ),
                },
            )
            self.assertEqual(
                bundle.evaluator["status"],
                "temporary",
            )
            self.assertEqual(
                len(manifest.images),
                1,
            )
            self.assertIsNone(
                manifest.images[0].digest
            )


if __name__ == "__main__":
    unittest.main()
