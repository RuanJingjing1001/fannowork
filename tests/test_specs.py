from pathlib import Path
import json
import tempfile
import unittest

from fanno_work.models import EnvironmentSpec
from fanno_work.specs import SpecError, load_environment, load_task, validate_pair


EXAMPLE = Path(__file__).parents[1] / "examples" / "file_delivery"


class SpecTests(unittest.TestCase):

    def test_task_can_load_external_initial_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "seed.json").write_text(
                json.dumps({"schema_version": 1, "collections": []}),
                encoding="utf-8",
            )
            (root / "task.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "id": "external-seed",
                        "title": "External seed",
                        "instruction": "Test external state.",
                        "environment_id": "test.environment",
                        "inputs": [],
                        "outputs": [],
                        "verifier": {"adapter": "required_artifacts"},
                        "initial_state_file": "seed.json",
                    }
                ),
                encoding="utf-8",
            )

            task = load_task(root / "task.json")

            self.assertEqual(
                task.initial_state,
                {"schema_version": 1, "collections": []},
            )

    def test_external_initial_state_cannot_escape_task_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_root = root / "task"
            task_root.mkdir()
            (root / "seed.json").write_text("{}", encoding="utf-8")
            (task_root / "task.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "id": "escaping-seed",
                        "title": "Escaping seed",
                        "instruction": "Reject an unsafe seed path.",
                        "environment_id": "test.environment",
                        "inputs": [],
                        "outputs": [],
                        "verifier": {"adapter": "required_artifacts"},
                        "initial_state_file": "../seed.json",
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(SpecError, "escapes task directory"):
                load_task(task_root / "task.json")

    def test_environment_mismatch_is_rejected(self) -> None:
        task = load_task(EXAMPLE / "task.json")
        environment = EnvironmentSpec(1, "different", "workspace", "1.0.0")
        with self.assertRaisesRegex(SpecError, "requires environment"):
            validate_pair(task, environment)

    def test_example_specs_are_native_and_compatible(self) -> None:
        task = load_task(EXAMPLE / "task.json")
        environment = load_environment(EXAMPLE / "environment.json")
        validate_pair(task, environment)

        self.assertEqual(task.taxonomy, {})
        self.assertEqual(environment.adapter, "workspace")
        self.assertEqual(
        environment.source_dir,
        EXAMPLE.resolve(),
        )
