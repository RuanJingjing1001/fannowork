from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from fanno_work.models import (
    ActionRequest,
    InputArtifact,
    TaskSpec,
    VerificationSpec,
)
from fanno_work.specs import load_environment

from fanno_legal.runtime.environment import (
    LegalOfficeEnvironment,
)
from fanno_legal.state.audit import AuditLog
from fanno_legal.state.database import (
    StateDatabase,
)


LEGAL_ROOT = Path(__file__).parents[2]
ENV_DIR = (
    LEGAL_ROOT
    / "environments"
    / "legal-office"
    / "v1"
)
FIXTURES = Path(__file__).parents[1] / "fixtures"


def load_seed(name: str) -> dict:
    return json.loads(
        (FIXTURES / name).read_text(
            encoding="utf-8"
        )
    )


def make_task(
    task_id: str,
    seed: dict,
    *,
    inputs: tuple[InputArtifact, ...] = (),
    source_dir: Path = Path("."),
) -> TaskSpec:
    return TaskSpec(
        schema_version=1,
        id=task_id,
        title="Legal office environment test",
        instruction=(
            "Inspect and update the permitted record."
        ),
        environment_id="legal.office.v1",
        inputs=inputs,
        outputs=(),
        verifier=VerificationSpec(
            adapter="test"
        ),
        initial_state=seed,
        source_dir=source_dir,
    )


class LegalOfficeEnvironmentTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.temporary_directory = (
            tempfile.TemporaryDirectory()
        )
        self.root = Path(
            self.temporary_directory.name
        )
        self.spec = load_environment(
            ENV_DIR / "environment.json"
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_prepare_exposes_profile_enabled_actions(
        self,
    ) -> None:
        environment = LegalOfficeEnvironment(
            self.spec
        )
        environment.prepare(
            make_task(
                "seed-a",
                load_seed("seed-a.json"),
            ),
            self.root / "run-a",
        )

        observation = environment.observe()

        self.assertEqual(
            observation.state_version,
            0,
        )
        self.assertEqual(
            [
                item.name
                for item
                in observation.available_actions
            ],
            [
                "filesystem.list",
                "filesystem.read",
                "filesystem.write",
                "document.extract_text",
                "document.convert",
                "document.append_paragraph",
                "document.create",
                "document.insert_heading",
                "document.insert_paragraph",
                "document.add_table",
                "document.save_as",
                "spreadsheet.inspect_workbook",
                "spreadsheet.read_range",
                "spreadsheet.find",
                "records.get",
                "records.query",
                "records.update",
            ],
        )

    def test_enabled_action_must_be_declared(
        self,
    ) -> None:
        invalid_spec = replace(
            self.spec,
            config={
                **self.spec.config,
                "enabled_actions": [
                    "missing.action"
                ],
            },
        )

        with self.assertRaisesRegex(
            ValueError,
            "not declared",
        ):
            LegalOfficeEnvironment(
                invalid_spec
            )

    def test_enabled_action_must_have_implementation(
        self,
    ) -> None:
        environment_dir = (
            self.root / "environment"
        )
        environment_dir.mkdir()

        actions = json.loads(
            (
                ENV_DIR / "actions.json"
            ).read_text(encoding="utf-8")
        )
        actions["actions"].append(
            {
                "id": "records.create",
                "description": (
                    "Deliberately unimplemented test action"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "effect": "create_record",
                "errors": [
                    "not_implemented"
                ],
            }
        )
        (
            environment_dir / "actions.json"
        ).write_text(
            json.dumps(actions),
            encoding="utf-8",
        )

        invalid_spec = replace(
            self.spec,
            source_dir=environment_dir,
            config={
                **self.spec.config,
                "actions_file": "actions.json",
                "enabled_actions": [
                    "records.create"
                ],
            },
        )

        with self.assertRaisesRegex(
            ValueError,
            "no runtime implementation",
        ):
            LegalOfficeEnvironment(
                invalid_spec
            )

    def test_prepare_materializes_input(
        self,
    ) -> None:
        task_dir = self.root / "task"
        task_dir.mkdir()

        (task_dir / "brief.txt").write_text(
            "Review the title report.",
            encoding="utf-8",
        )

        task = make_task(
            "with-input",
            load_seed("seed-a.json"),
            inputs=(
                InputArtifact(
                    path="brief.txt",
                    source="brief.txt",
                    media_type="text/plain",
                ),
            ),
            source_dir=task_dir,
        )

        environment = LegalOfficeEnvironment(
            self.spec
        )
        run_dir = self.root / "run-input"
        environment.prepare(
            task,
            run_dir,
        )

        copied = (
            run_dir
            / "workspace"
            / "inputs"
            / "brief.txt"
        )

        self.assertTrue(copied.is_file())
        self.assertEqual(
            copied.read_text(
                encoding="utf-8"
            ),
            "Review the title report.",
        )

        result = environment.act(
            ActionRequest(
                "filesystem.read",
                {
                    "path": "inputs/brief.txt"
                },
            )
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            result.output["content"],
            "Review the title report.",
        )

    def test_independent_client_observes_agent_update(
        self,
    ) -> None:
        run_dir = self.root / "run-a"
        environment = LegalOfficeEnvironment(
            self.spec
        )
        environment.prepare(
            make_task(
                "seed-a",
                load_seed("seed-a.json"),
            ),
            run_dir,
        )

        result = environment.act(
            ActionRequest(
                "records.update",
                {
                    "collection": "transactions",
                    "record_id": "TX-TEST-001",
                    "fields": {
                        "status": "reviewed"
                    },
                },
            )
        )

        independent_client = StateDatabase(
            run_dir
            / "state"
            / "state.db"
        )
        record = (
            independent_client.get_record(
                "transactions",
                "TX-TEST-001",
            )
        )

        self.assertTrue(result.ok)
        self.assertIsNotNone(record)
        self.assertEqual(
            record["fields"]["status"],
            "reviewed",
        )

    def test_reset_restores_initial_business_state(
        self,
    ) -> None:
        run_dir = self.root / "run-a"
        environment = LegalOfficeEnvironment(
            self.spec
        )
        environment.prepare(
            make_task(
                "seed-a",
                load_seed("seed-a.json"),
            ),
            run_dir,
        )

        initial = environment.snapshot()

        environment.act(
            ActionRequest(
                "records.update",
                {
                    "collection": "transactions",
                    "record_id": "TX-TEST-001",
                    "fields": {
                        "status": "reviewed"
                    },
                },
            )
        )

        environment.reset()
        restored = environment.snapshot()

        events = AuditLog(
            run_dir
            / "audit"
            / "actions.jsonl"
        ).read_all()

        self.assertEqual(
            restored["digest"],
            initial["digest"],
        )
        self.assertEqual(
            restored["state_version"],
            0,
        )
        self.assertEqual(
            events[-1]["action"],
            "environment.reset",
        )

    def test_filesystem_snapshot_and_reset(
        self,
    ) -> None:
        task_dir = self.root / "task"
        task_dir.mkdir()

        (task_dir / "brief.txt").write_text(
            "Initial evidence",
            encoding="utf-8",
        )

        task = make_task(
            "filesystem-reset",
            load_seed("seed-a.json"),
            inputs=(
                InputArtifact(
                    path="brief.txt",
                    source="brief.txt",
                    media_type="text/plain",
                ),
            ),
            source_dir=task_dir,
        )

        run_dir = self.root / "run-files"
        environment = LegalOfficeEnvironment(
            self.spec
        )
        environment.prepare(
            task,
            run_dir,
        )

        initial = environment.snapshot()

        result = environment.act(
            ActionRequest(
                "filesystem.write",
                {
                    "path": (
                        "outputs/risk_note.md"
                    ),
                    "content": "# Risk note\n",
                },
            )
        )

        changed = environment.snapshot()

        self.assertTrue(result.ok)
        self.assertIn(
            "inputs/brief.txt",
            changed["files"],
        )
        self.assertIn(
            "outputs/risk_note.md",
            changed["files"],
        )
        self.assertNotEqual(
            changed["digest"],
            initial["digest"],
        )
        self.assertEqual(
            changed["state_version"],
            1,
        )

        environment.reset()
        restored = environment.snapshot()

        self.assertEqual(
            restored["digest"],
            initial["digest"],
        )
        self.assertEqual(
            restored["state_version"],
            0,
        )
        self.assertIn(
            "inputs/brief.txt",
            restored["files"],
        )
        self.assertNotIn(
            "outputs/risk_note.md",
            restored["files"],
        )
        self.assertFalse(
            (
                run_dir
                / "workspace"
                / "outputs"
                / "risk_note.md"
            ).exists()
        )

    def test_different_seed_requires_no_runtime_change(
        self,
    ) -> None:
        environment = LegalOfficeEnvironment(
            self.spec
        )
        environment.prepare(
            make_task(
                "seed-b",
                load_seed("seed-b.json"),
            ),
            self.root / "run-b",
        )

        result = environment.act(
            ActionRequest(
                "records.get",
                {
                    "collection": "matters",
                    "record_id": "MATTER-002",
                },
            )
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            result.output[
                "record"
            ]["fields"]["matter_type"],
            "employment",
        )

    def test_unknown_action_is_rejected_and_audited(
        self,
    ) -> None:
        run_dir = self.root / "run-a"
        environment = LegalOfficeEnvironment(
            self.spec
        )
        environment.prepare(
            make_task(
                "seed-a",
                load_seed("seed-a.json"),
            ),
            run_dir,
        )

        result = environment.act(
            ActionRequest(
                "records.create",
                {},
            )
        )

        events = AuditLog(
            run_dir
            / "audit"
            / "actions.jsonl"
        ).read_all()

        self.assertFalse(result.ok)
        self.assertEqual(
            result.error,
            "unknown_action",
        )
        self.assertEqual(
            events[-1]["action"],
            "records.create",
        )


if __name__ == "__main__":
    unittest.main()
