import json
from pathlib import Path
import tempfile
import unittest

from fanno_work.agents import ScriptedAgent
from fanno_work.models import ActionRequest
from fanno_work.registry import default_registry
from fanno_work.runtime import WorkflowRunner
from fanno_work.specs import load_environment, load_task


EXAMPLE = Path(__file__).parents[1] / "examples" / "file_delivery"


class WorkspaceTests(unittest.TestCase):
    def test_full_lifecycle_preserves_artifact_and_verifies_reset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            task = load_task(EXAMPLE / "task.json")
            environment = load_environment(EXAMPLE / "environment.json")
            agent = ScriptedAgent(
                [
                    ActionRequest("read_file", {"path": "inputs/brief.txt"}),
                    ActionRequest(
                        "write_file",
                        {"path": "outputs/note.txt", "content": "Delivery accepted."},
                    ),
                ]
            )
            manifest = WorkflowRunner(default_registry()).run(
                task, environment, agent, tmp_path / "run"
            )
            self.assertEqual(manifest["status"], "passed")
            self.assertTrue(manifest["verification"]["passed"])
            self.assertTrue(manifest["reset"]["passed"])
            self.assertEqual(
                (tmp_path / "run" / "artifacts" / "note.txt").read_text(),
                "Delivery accepted.",
            )
            events = [
                json.loads(line)["type"]
                for line in (tmp_path / "run" / "trajectory.jsonl").read_text().splitlines()
            ]
            self.assertEqual(events.count("action"), 2)
            self.assertIn("verification", events)
            self.assertEqual(events[-1], "reset")

    def test_missing_required_artifact_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task = load_task(EXAMPLE / "task.json")
            environment = load_environment(EXAMPLE / "environment.json")
            manifest = WorkflowRunner(default_registry()).run(
                task,
                environment,
                ScriptedAgent([]),
                Path(directory) / "run",
            )
            self.assertEqual(manifest["status"], "failed")
            self.assertFalse(manifest["verification"]["passed"])
            self.assertEqual(manifest["verification"]["score"], 0.0)
            self.assertTrue(manifest["reset"]["passed"])

    def test_workspace_rejects_output_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            task = load_task(EXAMPLE / "task.json")
            environment_spec = load_environment(EXAMPLE / "environment.json")
            environment = default_registry().create_environment(environment_spec)
            environment.prepare(task, tmp_path / "run")
            result = environment.act(
                ActionRequest(
                    "write_file",
                    {"path": "outputs/../../escape.txt", "content": "x"},
                )
            )
            self.assertFalse(result.ok)
            self.assertFalse((tmp_path / "escape.txt").exists())
