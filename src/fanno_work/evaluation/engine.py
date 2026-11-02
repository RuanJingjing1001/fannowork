from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any

from .checker import CheckerRegistry
from .checkers import (
    default_checker_registry,
)
from .context import EvaluationContext
from .models import (
    CheckerResult,
    EvaluatorSpec,
    RewardResult,
    load_evaluator_spec,
)


def _write_json(
    path: Path,
    value: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_suffix(
        path.suffix + ".tmp"
    )

    temporary.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    temporary.replace(path)


class RewardEngine:
    def __init__(
        self,
        registry: CheckerRegistry | None = None,
    ) -> None:
        self.registry = (
            registry
            or default_checker_registry()
        )

    def evaluate(
        self,
        context: EvaluationContext,
        evaluator: EvaluatorSpec | str | Path,
        *,
        output_dir: str | Path | None = None,
    ) -> RewardResult:
        if not isinstance(
            evaluator,
            EvaluatorSpec,
        ):
            evaluator = load_evaluator_spec(
                evaluator
            )

        results: list[CheckerResult] = []

        for spec in evaluator.checks:
            started = time.monotonic()

            try:
                checker = self.registry.get(
                    spec.type
                )
                result = checker.check(
                    context,
                    spec,
                )
            except Exception as exc:
                result = CheckerResult(
                    id=spec.id,
                    type=spec.type,
                    component=spec.component,
                    passed=False,
                    score=0.0,
                    weight=spec.weight,
                    hard=spec.hard,
                    message=(
                        "checker execution failed"
                    ),
                    evidence={},
                    error=(
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    ),
                    duration_ms=(
                        time.monotonic()
                        - started
                    )
                    * 1000,
                )

            results.append(result)

        hard_constraints_passed = all(
            result.passed
            for result in results
            if result.hard
        )

        weighted_results = [
            result
            for result in results
            if result.weight > 0
        ]

        total_weight = sum(
            result.weight
            for result in weighted_results
        )

        if total_weight > 0:
            base_reward = sum(
                result.score
                * result.weight
                for result in weighted_results
            ) / total_weight
        else:
            base_reward = (
                1.0
                if hard_constraints_passed
                else 0.0
            )

        if hard_constraints_passed:
            reward = base_reward
        else:
            reward = min(
                base_reward,
                evaluator.policy
                .hard_failure_reward,
            )

        reward = max(
            0.0,
            min(1.0, reward),
        )

        component_names = sorted(
            {
                result.component
                for result in results
            }
        )

        components: dict[str, float] = {}

        for component in component_names:
            component_results = [
                result
                for result in results
                if result.component == component
                and result.weight > 0
            ]

            component_weight = sum(
                result.weight
                for result in component_results
            )

            if component_weight > 0:
                component_score = sum(
                    result.score
                    * result.weight
                    for result
                    in component_results
                ) / component_weight
            else:
                component_score = (
                    1.0
                    if all(
                        result.passed
                        for result in results
                        if (
                            result.component
                            == component
                        )
                    )
                    else 0.0
                )

            components[component] = (
                component_score
            )

        passed = (
            hard_constraints_passed
            and reward
            >= evaluator.policy.pass_threshold
        )

        evidence_refs = tuple(
            f"evidence.json#{result.id}"
            for result in results
        )

        reward_result = RewardResult(
            schema_version=1,
            task_id=context.task_id,
            evaluator_id=evaluator.id,
            evaluator_version=(
                evaluator.version
            ),
            passed=passed,
            hard_constraints_passed=(
                hard_constraints_passed
            ),
            reward=reward,
            components=components,
            checks=tuple(results),
            evidence_refs=evidence_refs,
            metadata={
                "evaluator_status": (
                    evaluator.status
                ),
                "aggregation": (
                    evaluator.policy.aggregation
                ),
                "pass_threshold": (
                    evaluator.policy
                    .pass_threshold
                ),
            },
        )

        destination = (
            Path(output_dir).resolve()
            if output_dir is not None
            else context.evaluation_dir
        )

        result_data = reward_result.to_dict()

        evidence_data = {
            "schema_version": 1,
            "task_id": context.task_id,
            "evaluator_id": evaluator.id,
            "checks": {
                result.id: {
                    "passed": result.passed,
                    "score": result.score,
                    "message": result.message,
                    "error": result.error,
                    "evidence": result.evidence,
                }
                for result in results
            },
        }

        _write_json(
            destination / "result.json",
            result_data,
        )
        _write_json(
            destination / "evidence.json",
            evidence_data,
        )

        return reward_result