from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import ActionRequest, ActionResult, Observation, TaskSpec


class Agent(ABC):
    """Decision boundary for model, human, replay, or scripted agents."""

    @abstractmethod
    def start(self, task: TaskSpec, observation: Observation) -> None:
        """Initialize agent-local state for one run."""

    @abstractmethod
    def next_action(
        self, observation: Observation, previous_result: ActionResult | None
    ) -> ActionRequest | None:
        """Return the next action, or None to end the run."""

    def close(self) -> None:
        return None
