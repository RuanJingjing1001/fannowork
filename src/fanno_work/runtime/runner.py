from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from ..agents import Agent
from ..models import ActionResult, EnvironmentSpec, TaskSpec, to_jsonable
from ..registry import PluginRegistry
from ..specs import validate_pair
from .recorder import TrajectoryRecorder


class WorkflowRunner:
    def __init__(self, registry: PluginRegistry):
        self.registry = registry

    def run(
        self,
        task: TaskSpec,
        environment_spec: EnvironmentSpec,
        agent: Agent,
        run_dir: Path,
        verify_reset: bool = True,
    ) -> dict:
        validate_pair(task, environment_spec)
        run_dir = run_dir.resolve()
        environment = self.registry.create_environment(environment_spec)
        verifier = self.registry.create_verifier(task.verifier)
        started_at = datetime.now(timezone.utc).isoformat()
        environment.prepare(task, run_dir)
        recorder = TrajectoryRecorder(run_dir / "trajectory.jsonl")
        initial = environment.snapshot()
        observation = environment.observe()
        recorder.append("observation", observation)
        agent.start(task, observation)
        action_count = 0
        previous_result: ActionResult | None = None
        execution_error: str | None = None
        try:
            while action_count < 1000:
                request = agent.next_action(observation, previous_result)
                if request is None:
                    break
                recorder.append("action", request)
                result = environment.act(request)
                recorder.append("action_result", result)
                action_count += 1
                previous_result = result
                if not result.ok:
                    execution_error = result.error or "action failed"
                    break
                observation = environment.observe()
                recorder.append("observation", observation)
            else:
                execution_error = "maximum action count exceeded"
            final = environment.snapshot()
            verification = verifier.verify(task, environment, run_dir)
            recorder.append("verification", verification)
            artifact_dir = run_dir / "artifacts"
            output_dir = run_dir / "workspace" / "outputs"
            if output_dir.exists():
                shutil.copytree(output_dir, artifact_dir)
            reset_check = {"checked": False, "passed": None}
            if verify_reset:
                environment.reset()
                after_reset = environment.snapshot()
                reset_check = {
                    "checked": True,
                    "passed": after_reset["digest"] == initial["digest"],
                    "initial_digest": initial["digest"],
                    "reset_digest": after_reset["digest"],
                }
                recorder.append("reset", reset_check)
        finally:
            agent.close()
            environment.close()
        status = (
            "passed"
            if verification.passed
            and reset_check.get("passed") is not False
            and execution_error is None
            else "failed"
        )
        manifest = {
            "schema_version": 1,
            "task_id": task.id,
            "environment_id": environment_spec.id,
            "environment_adapter": environment_spec.adapter,
            "environment_version": environment_spec.version,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "action_count": action_count,
            "execution_error": execution_error,
            "initial_state": initial,
            "final_state": final,
            "verification": to_jsonable(verification),
            "reset": reset_check,
            "trajectory": "trajectory.jsonl",
            "artifacts": "artifacts/",
        }
        (run_dir / "run.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return manifest
