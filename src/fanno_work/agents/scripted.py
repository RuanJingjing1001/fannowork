from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from ..models import ActionRequest, ActionResult, Observation, TaskSpec
from .base import Agent


class ScriptedAgent(Agent):
    """Deterministic agent for smoke tests, reference solutions, and replay."""

    def __init__(self, actions: Iterable[ActionRequest]):
        self._initial_actions = tuple(actions)
        self._actions: deque[ActionRequest] = deque()

    def start(self, task: TaskSpec, observation: Observation) -> None:
        self._actions = deque(self._initial_actions)

    def next_action(
        self, observation: Observation, previous_result: ActionResult | None
    ) -> ActionRequest | None:
        return self._actions.popleft() if self._actions else None
