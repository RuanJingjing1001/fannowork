from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any


class EvaluationSpecError(ValueError):
    pass


@dataclass(frozen=True)
class CheckSpec:
    id: str
    type: str
    component: str
    weight: float
    hard: bool
    config: dict[str, Any] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class RewardPolicySpec:
    aggregation: str = "weighted_mean"
    pass_threshold: float = 0.8
    hard_failure_reward: float = 0.0


@dataclass(frozen=True)
class EvaluatorSpec:
    schema_version: int
    id: str
    version: str
    adapter: str
    status: str
    policy: RewardPolicySpec
    checks: tuple[CheckSpec, ...]
    metadata: dict[str, Any] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class CheckerResult:
    id: str
    type: str
    component: str
    passed: bool
    score: float
    weight: float
    hard: bool
    message: str
    evidence: dict[str, Any] = field(
        default_factory=dict
    )
    error: str | None = None
    duration_ms: float = 0.0


@dataclass(frozen=True)
class RewardResult:
    schema_version: int
    task_id: str
    evaluator_id: str
    evaluator_version: str
    passed: bool
    hard_constraints_passed: bool
    reward: float
    components: dict[str, float]
    checks: tuple[CheckerResult, ...]
    evidence_refs: tuple[str, ...]
    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _required_string(
    value: dict[str, Any],
    key: str,
) -> str:
    result = value.get(key)

    if not isinstance(result, str) or not result:
        raise EvaluationSpecError(
            f"evaluator field {key!r} "
            "must be a non-empty string"
        )

    return result


def parse_evaluator_spec(
    value: dict[str, Any],
) -> EvaluatorSpec:
    schema_version = value.get(
        "schema_version"
    )

    if schema_version != 1:
        raise EvaluationSpecError(
            "unsupported evaluator schema_version: "
            f"{schema_version!r}"
        )

    adapter = _required_string(
        value,
        "adapter",
    )

    if adapter != "reward_engine":
        raise EvaluationSpecError(
            "evaluator adapter must be "
            "'reward_engine'"
        )

    raw_policy = value.get("policy", {})

    if not isinstance(raw_policy, dict):
        raise EvaluationSpecError(
            "evaluator policy must be an object"
        )

    aggregation = raw_policy.get(
        "aggregation",
        "weighted_mean",
    )

    if aggregation != "weighted_mean":
        raise EvaluationSpecError(
            "only weighted_mean aggregation "
            "is currently supported"
        )

    pass_threshold = raw_policy.get(
        "pass_threshold",
        0.8,
    )
    hard_failure_reward = raw_policy.get(
        "hard_failure_reward",
        0.0,
    )

    if not isinstance(
        pass_threshold,
        (int, float),
    ):
        raise EvaluationSpecError(
            "pass_threshold must be numeric"
        )

    if not 0.0 <= float(pass_threshold) <= 1.0:
        raise EvaluationSpecError(
            "pass_threshold must be between 0 and 1"
        )

    if not isinstance(
        hard_failure_reward,
        (int, float),
    ):
        raise EvaluationSpecError(
            "hard_failure_reward must be numeric"
        )

    if not (
        0.0
        <= float(hard_failure_reward)
        <= 1.0
    ):
        raise EvaluationSpecError(
            "hard_failure_reward must be "
            "between 0 and 1"
        )

    raw_checks = value.get("checks")

    if not isinstance(raw_checks, list):
        raise EvaluationSpecError(
            "evaluator checks must be a list"
        )

    checks: list[CheckSpec] = []
    check_ids: set[str] = set()

    for index, raw_check in enumerate(
        raw_checks
    ):
        if not isinstance(raw_check, dict):
            raise EvaluationSpecError(
                f"check {index} must be an object"
            )

        check_id = _required_string(
            raw_check,
            "id",
        )

        if check_id in check_ids:
            raise EvaluationSpecError(
                f"duplicate check id: {check_id}"
            )

        check_ids.add(check_id)

        checker_type = _required_string(
            raw_check,
            "type",
        )

        component = raw_check.get(
            "component",
            "general",
        )

        if not isinstance(
            component,
            str,
        ) or not component:
            raise EvaluationSpecError(
                f"check {check_id} component "
                "must be a non-empty string"
            )

        weight = raw_check.get(
            "weight",
            1.0,
        )

        if not isinstance(weight, (int, float)):
            raise EvaluationSpecError(
                f"check {check_id} weight "
                "must be numeric"
            )

        if float(weight) < 0:
            raise EvaluationSpecError(
                f"check {check_id} weight "
                "must not be negative"
            )

        hard = raw_check.get(
            "hard",
            False,
        )

        if not isinstance(hard, bool):
            raise EvaluationSpecError(
                f"check {check_id} hard "
                "must be boolean"
            )

        config = raw_check.get(
            "config",
            {},
        )

        if not isinstance(config, dict):
            raise EvaluationSpecError(
                f"check {check_id} config "
                "must be an object"
            )

        checks.append(
            CheckSpec(
                id=check_id,
                type=checker_type,
                component=component,
                weight=float(weight),
                hard=hard,
                config=dict(config),
            )
        )

    if not checks:
        raise EvaluationSpecError(
            "evaluator must contain at least "
            "one check"
        )

    metadata = value.get(
        "metadata",
        {},
    )

    if not isinstance(metadata, dict):
        raise EvaluationSpecError(
            "evaluator metadata must be an object"
        )

    return EvaluatorSpec(
        schema_version=1,
        id=_required_string(value, "id"),
        version=_required_string(
            value,
            "version",
        ),
        adapter=adapter,
        status=_required_string(
            value,
            "status",
        ),
        policy=RewardPolicySpec(
            aggregation=aggregation,
            pass_threshold=float(
                pass_threshold
            ),
            hard_failure_reward=float(
                hard_failure_reward
            ),
        ),
        checks=tuple(checks),
        metadata=dict(metadata),
    )


def load_evaluator_spec(
    path: str | Path,
) -> EvaluatorSpec:
    resolved = Path(path).resolve()

    try:
        value = json.loads(
            resolved.read_text(
                encoding="utf-8"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ) as exc:
        raise EvaluationSpecError(
            f"cannot read evaluator {resolved}: "
            f"{exc}"
        ) from exc

    if not isinstance(value, dict):
        raise EvaluationSpecError(
            "evaluator file must contain "
            "a JSON object"
        )

    return parse_evaluator_spec(value)