from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from fanno_work.agents import ScriptedAgent
from fanno_work.models import (
    ActionRequest,
    InputArtifact,
    OutputArtifact,
    TaskSpec,
    VerificationSpec,
)
from fanno_work.registry import default_registry
from fanno_work.runtime import WorkflowRunner
from fanno_work.specs import load_environment

from fanno_legal.state.audit import AuditLog
from fanno_legal.state.database import StateDatabase
from fanno_legal.plugin import register_legal_plugin


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


class LegalRunnerTests(unittest.TestCase):
    def test_file_and_records_workflow_runs_end_to_end(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_dir = root / "task"
            run_dir = root / "run"

            task_dir.mkdir()

            input_text = (
                "Transaction TX-TEST-001 requires "
                "a title-risk review before signing."
            )

            (
                task_dir / "transaction_brief.txt"
            ).write_text(
                input_text,
                encoding="utf-8",
            )

            environment_spec = load_environment(
                ENV_DIR / "environment.json"
            )

            task = TaskSpec(
                schema_version=1,
                id="legal-combined-smoke-test",
                title=(
                    "Review and update a legal transaction"
                ),
                instruction=(
                    "Read the transaction brief, inspect "
                    "TX-TEST-001, mark it reviewed, and "
                    "write outputs/risk_note.md."
                ),
                environment_id="legal.office.v1",
                inputs=(
                    InputArtifact(
                        path="transaction_brief.txt",
                        source="transaction_brief.txt",
                        media_type="text/plain",
                    ),
                ),
                outputs=(
                    OutputArtifact(
                        path="risk_note.md",
                        media_type="text/markdown",
                        required=True,
                    ),
                ),
                verifier=VerificationSpec(
                    adapter="required_artifacts",
                    config={},
                ),
                initial_state=load_seed(
                    "seed-a.json"
                ),
                source_dir=task_dir,
            )

            output_content = (
                "# Transaction Risk Note\n\n"
                "TX-TEST-001 was reviewed.\n\n"
                "The title risk must be resolved "
                "before signing.\n"
            )

            agent = ScriptedAgent(
                [
                    ActionRequest(
                        "filesystem.read",
                        {
                            "path": (
                                "inputs/"
                                "transaction_brief.txt"
                            )
                        },
                    ),
                    ActionRequest(
                        "document.extract_text",
                        {
                            "path": (
                                "inputs/"
                                "transaction_brief.txt"
                            )
                        },
                    ),
                    ActionRequest(
                        "records.get",
                        {
                            "collection": "transactions",
                            "record_id": "TX-TEST-001",
                        },
                    ),
                    ActionRequest(
                        "records.update",
                        {
                            "collection": "transactions",
                            "record_id": "TX-TEST-001",
                            "fields": {
                                "status": "reviewed",
                                "risk_notes": (
                                    "Title risk must be "
                                    "resolved before signing."
                                ),
                            },
                        },
                    ),
                    ActionRequest(
                        "filesystem.write",
                        {
                            "path": (
                                "outputs/risk_note.md"
                            ),
                            "content": output_content,
                        },
                    ),
                ]
            )

            registry = default_registry()
            register_legal_plugin(registry)

            manifest = WorkflowRunner(
                registry
            ).run(
                task=task,
                environment_spec=environment_spec,
                agent=agent,
                run_dir=run_dir,
            )

            # 整个执行流程应当成功。
            self.assertEqual(
                manifest["status"],
                "passed",
            )
            self.assertEqual(
                manifest["action_count"],
                5,
            )
            self.assertIsNone(
                manifest["execution_error"]
            )

            # RequiredArtifactsVerifier 应当看到输出。
            self.assertTrue(
                manifest["verification"]["passed"]
            )
            self.assertEqual(
                manifest["verification"]["score"],
                1.0,
            )

            # reset 之前的最终状态应包含两次写操作：
            # 一次数据库更新，一次文件写入。
            final_state = manifest["final_state"]

            self.assertEqual(
                final_state["state_version"],
                2,
            )
            self.assertIn(
                "inputs/transaction_brief.txt",
                final_state["files"],
            )
            self.assertIn(
                "outputs/risk_note.md",
                final_state["files"],
            )

            final_record = (
                final_state
                ["collections"][0]
                ["records"][0]
            )

            self.assertEqual(
                final_record["fields"]["status"],
                "reviewed",
            )
            self.assertEqual(
                final_record["fields"]["risk_notes"],
                (
                    "Title risk must be resolved "
                    "before signing."
                ),
            )

            # Runner 应在 reset 前把 outputs 复制到 artifacts。
            artifact = (
                run_dir
                / "artifacts"
                / "risk_note.md"
            )

            self.assertTrue(
                artifact.is_file()
            )
            self.assertEqual(
                artifact.read_text(
                    encoding="utf-8"
                ),
                output_content,
            )

            # Runner 结束后，工作区已经 reset。
            workspace_input = (
                run_dir
                / "workspace"
                / "inputs"
                / "transaction_brief.txt"
            )
            workspace_output = (
                run_dir
                / "workspace"
                / "outputs"
                / "risk_note.md"
            )

            self.assertTrue(
                workspace_input.is_file()
            )
            self.assertEqual(
                workspace_input.read_text(
                    encoding="utf-8"
                ),
                input_text,
            )
            self.assertFalse(
                workspace_output.exists()
            )

            # artifacts 独立于工作区 reset，应继续保留。
            self.assertTrue(
                artifact.is_file()
            )

            # 独立数据库客户端应看到恢复后的初始状态。
            independent_client = StateDatabase(
                run_dir
                / "state"
                / "state.db"
            )

            restored_record = (
                independent_client.get_record(
                    "transactions",
                    "TX-TEST-001",
                )
            )

            self.assertIsNotNone(
                restored_record
            )
            self.assertEqual(
                restored_record[
                    "fields"
                ]["status"],
                "under_review",
            )
            self.assertEqual(
                restored_record[
                    "fields"
                ]["risk_notes"],
                "",
            )
            self.assertEqual(
                independent_client.get_state_version(),
                0,
            )

            # Runner 的 digest reset 检查应通过。
            self.assertTrue(
                manifest["reset"]["checked"]
            )
            self.assertTrue(
                manifest["reset"]["passed"]
            )
            self.assertEqual(
                manifest["reset"][
                    "initial_digest"
                ],
                manifest["reset"][
                    "reset_digest"
                ],
            )

            # 审计应包含五个 Agent Action 和 reset。
            audit_events = AuditLog(
                run_dir
                / "audit"
                / "actions.jsonl"
            ).read_all()

            self.assertEqual(
                [
                    event["action"]
                    for event in audit_events
                ],
                [
                    "filesystem.read",
                    "document.extract_text",
                    "records.get",
                    "records.update",
                    "filesystem.write",
                    "environment.reset",
                ],
            )

            self.assertFalse(
                audit_events[0][
                    "state_changed"
                ]
            )
            self.assertFalse(
                audit_events[1][
                    "state_changed"
                ]
            )
            self.assertFalse(
                audit_events[2][
                    "state_changed"
                ]
            )
            self.assertTrue(
                audit_events[3][
                    "state_changed"
                ]
            )
            self.assertTrue(
                audit_events[4][
                    "state_changed"
                ]
            )
            self.assertTrue(
                audit_events[5][
                    "state_changed"
                ]
            )

            # 文件写入审计不能保存完整法律文档。
            write_arguments = (
                audit_events[4]["arguments"]
            )

            self.assertNotIn(
                "content",
                write_arguments,
            )
            self.assertEqual(
                write_arguments["path"],
                "outputs/risk_note.md",
            )
            self.assertIn(
                "content_sha256",
                write_arguments,
            )
            self.assertIn(
                "content_bytes",
                write_arguments,
            )

            # 通用 Runner 运行记录应存在。
            run_manifest_path = (
                run_dir / "run.json"
            )
            trajectory_path = (
                run_dir / "trajectory.jsonl"
            )

            self.assertTrue(
                run_manifest_path.is_file()
            )
            self.assertTrue(
                trajectory_path.is_file()
            )

            persisted_manifest = json.loads(
                run_manifest_path.read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(
                persisted_manifest["status"],
                "passed",
            )
            self.assertEqual(
                persisted_manifest[
                    "action_count"
                ],
                5,
            )

            trajectory = [
                json.loads(line)
                for line in (
                    trajectory_path.read_text(
                        encoding="utf-8"
                    ).splitlines()
                )
            ]

            event_types = [
                event["type"]
                for event in trajectory
            ]

            self.assertEqual(
                event_types.count("action"),
                5,
            )
            self.assertEqual(
                event_types.count(
                    "action_result"
                ),
                5,
            )
            self.assertIn(
                "verification",
                event_types,
            )
            self.assertEqual(
                event_types[-1],
                "reset",
            )


if __name__ == "__main__":
    unittest.main()
