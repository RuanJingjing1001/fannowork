from pathlib import Path
import unittest

from fanno_work.models import EnvironmentSpec
from fanno_work.specs import SpecError, load_environment, load_task, validate_pair


EXAMPLE = Path(__file__).parents[1] / "examples" / "file_delivery"


class SpecTests(unittest.TestCase):
    def test_example_specs_are_native_and_compatible(self) -> None:
        task = load_task(EXAMPLE / "task.json")
        environment = load_environment(EXAMPLE / "environment.json")
        validate_pair(task, environment)
        self.assertEqual(task.taxonomy, {})
        self.assertEqual(environment.adapter, "workspace")

    def test_environment_mismatch_is_rejected(self) -> None:
        task = load_task(EXAMPLE / "task.json")
        environment = EnvironmentSpec(1, "different", "workspace", "1.0.0")
        with self.assertRaisesRegex(SpecError, "requires environment"):
            validate_pair(task, environment)
