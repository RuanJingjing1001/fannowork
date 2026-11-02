from __future__ import annotations

from typing import Protocol

from .context import EvaluationContext
from .models import (
    CheckSpec,
    CheckerResult,
)


class Checker(Protocol):
    def check(
        self,
        context: EvaluationContext,
        spec: CheckSpec,
    ) -> CheckerResult:
        ...


class CheckerRegistry:
    def __init__(self) -> None:
        self._checkers: dict[
            str,
            Checker,
        ] = {}

    def register(
        self,
        checker_type: str,
        checker: Checker,
    ) -> None:
        if not checker_type:
            raise ValueError(
                "checker_type must not be empty"
            )

        if checker_type in self._checkers:
            raise ValueError(
                "checker type already registered: "
                f"{checker_type}"
            )

        self._checkers[
            checker_type
        ] = checker

    def get(
        self,
        checker_type: str,
    ) -> Checker:
        try:
            return self._checkers[
                checker_type
            ]
        except KeyError as exc:
            raise KeyError(
                "unknown checker type: "
                f"{checker_type}"
            ) from exc

    def describe(self) -> tuple[str, ...]:
        return tuple(
            sorted(self._checkers)
        )