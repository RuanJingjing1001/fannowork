from __future__ import annotations

from pathlib import Path

from ..environments.base import WorkflowEnvironment
from ..models import CheckResult, TaskSpec, VerificationResult, VerificationSpec
from .base import Verifier


class RequiredArtifactsVerifier(Verifier):
    def __init__(self, spec: VerificationSpec):
        self.spec = spec

    def verify(
        self, task: TaskSpec, environment: WorkflowEnvironment, run_dir: Path
    ) -> VerificationResult:
        output_root = run_dir / "workspace" / "outputs"
        checks: list[CheckResult] = []
        for artifact in task.outputs:
            path = (output_root / artifact.path).resolve()
            safe = path.is_relative_to(output_root.resolve())
            exists = safe and path.is_file()
            passed = exists or not artifact.required
            checks.append(
                CheckResult(
                    id=f"artifact:{artifact.path}",
                    passed=passed,
                    message="present" if exists else "missing",
                    evidence={"path": artifact.path, "required": artifact.required},
                )
            )
        passed_count = sum(check.passed for check in checks)
        score = passed_count / len(checks) if checks else 1.0
        return VerificationResult(
            passed=all(check.passed for check in checks),
            score=score,
            checks=tuple(checks),
            metadata={"adapter": "required_artifacts"},
        )
