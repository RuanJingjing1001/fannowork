from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..environments.base import WorkflowEnvironment
from ..models import TaskSpec, VerificationResult, VerificationSpec


class Verifier(ABC):
    @abstractmethod
    def verify(
        self, task: TaskSpec, environment: WorkflowEnvironment, run_dir: Path
    ) -> VerificationResult:
        """Score an immutable view of a completed run."""


VerifierFactory = type[Verifier]


def build_verifier(factory: VerifierFactory, spec: VerificationSpec) -> Verifier:
    return factory(spec)  # type: ignore[call-arg]
