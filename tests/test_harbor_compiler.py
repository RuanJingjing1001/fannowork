from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from fanno_work.integrations.harbor import (
    HarborTaskCompiler,
    validate_harbor_package,
)
from fanno_work.tasking import (
    TaskBundleResolver,
)


ROOT = Path(__file__).parents[1]

TASK_PATH = (
    ROOT
    / "legal"
    / "tasks"
    / "imported"
    / "harvey-lab"
    / "litigation-invoice-review"
    / "v1"
    / "task.json"
)

ENVIRONMENT_PATH = (
    ROOT
    / "legal"
    / "environments"
    / "legal-office"
    / "v1"
    / "environment.container.json"
)

EVALUATOR_PATH = (
    ROOT
    / "legal"
    / "evaluators"
    / "litigation-invoice-review"
    / "v1"
    / "evaluator.json"
)


class HarborTaskCompilerTests(
    unittest.TestCase
):
    def test_compiles_complete_harbor_task(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle_dir = root / "canonical"
            harbor_dir = root / "harbor"

            TaskBundleResolver().build(
                task_path=TASK_PATH,
                environment_path=(
                    ENVIRONMENT_PATH
                ),
                evaluator_path=(
                    EVALUATOR_PATH
                ),
                output_dir=bundle_dir,
            )

            package = (
                HarborTaskCompiler().compile(
                    bundle_dir=bundle_dir,
                    output_dir=harbor_dir,
                )
            )

            manifest = (
                validate_harbor_package(
                    harbor_dir
                )
            )

            self.assertEqual(
                package.source_bundle_id,
                (
                    "legal.harvey-lab."
                    "litigation-invoice-review.v1"
                ),
            )

            self.assertEqual(
                manifest["task_name"],
                package.task_name,
            )

            self.assertTrue(
                (
                    harbor_dir
                    / "task.toml"
                ).is_file()
            )

            self.assertTrue(
                (
                    harbor_dir
                    / "instruction.md"
                ).is_file()
            )

            self.assertTrue(
                (
                    harbor_dir
                    / "environment"
                    / "docker-compose.yaml"
                ).is_file()
            )

            self.assertTrue(
                (
                    harbor_dir
                    / "tests"
                    / "test.sh"
                ).is_file()
            )

            compose = (
                harbor_dir
                / "environment"
                / "docker-compose.yaml"
            ).read_text(
                encoding="utf-8"
            )

            self.assertIn(
                "  main:",
                compose,
            )
            self.assertIn(
                "  greenmail:",
                compose,
            )
            self.assertIn(
                "  radicale:",
                compose,
            )
            self.assertIn(
                (
                    "FANNO_GREENMAIL_HOST: "
                    "127.0.0.1"
                ),
                compose,
            )
            self.assertIn(
                (
                    "FANNO_RADICALE_URL: "
                    "http://127.0.0.1:5232"
                ),
                compose,
            )

            self.assertTrue(
                (
                    harbor_dir
                    / "tests"
                    / "bundle"
                    / "evaluator.json"
                ).is_file()
            )

            self.assertFalse(
                (
                    harbor_dir
                    / "environment"
                    / "evaluator.json"
                ).exists()
            )

            self.assertFalse(
                (
                    harbor_dir
                    / "environment"
                    / "public-inputs"
                    / "assets"
                    / "holt-email-research-"
                    "concerns.eml"
                ).exists()
            )

            self.assertTrue(
                (
                    harbor_dir
                    / "environment"
                    / "private"
                    / "assets"
                    / "holt-email-research-"
                    "concerns.eml"
                ).is_file()
            )

            test_mode = (
                harbor_dir
                / "tests"
                / "test.sh"
            ).stat().st_mode

            self.assertTrue(
                test_mode & 0o111
            )


if __name__ == "__main__":
    unittest.main()
