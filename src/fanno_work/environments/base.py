from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..models import ActionRequest, ActionResult, EnvironmentSpec, Observation, TaskSpec


class WorkflowEnvironment(ABC):
    """Runtime boundary implemented by every real or simulated environment."""

    @abstractmethod
    def prepare(self, task: TaskSpec, run_dir: Path) -> None:
        """Create a clean task instance in run_dir."""

    @abstractmethod
    def observe(self) -> Observation:
        """Return only state that an agent is allowed to see."""

    @abstractmethod
    def act(self, request: ActionRequest) -> ActionResult:
        """Execute one declared action."""

    @abstractmethod
    def snapshot(self) -> dict:
        """Return evaluator-visible state for hashing and verification."""

    @abstractmethod
    def reset(self) -> None:
        """Restore the prepared task to its initial state."""

    @abstractmethod
    def close(self) -> None:
        """Release runtime resources without deleting run artifacts."""


EnvironmentFactory = type[WorkflowEnvironment]


def build_environment(factory: EnvironmentFactory, spec: EnvironmentSpec) -> WorkflowEnvironment:
    return factory(spec)  # type: ignore[call-arg]
