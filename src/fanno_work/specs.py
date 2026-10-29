from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import (
    EnvironmentSpec,
    InputArtifact,
    OutputArtifact,
    TaskSpec,
    VerificationSpec,
)


class SpecError(ValueError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpecError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SpecError(f"{path} must contain a JSON object")
    return value


def _required(data: dict[str, Any], key: str, expected: type) -> Any:
    value = data.get(key)
    if not isinstance(value, expected):
        raise SpecError(f"field {key!r} must be {expected.__name__}")
    return value


def load_environment(path: str | Path) -> EnvironmentSpec:
    path = Path(path).resolve()
    data = _read_json(path)
    return EnvironmentSpec(
        schema_version=int(data.get("schema_version", 1)),
        id=_required(data, "id", str),
        adapter=_required(data, "adapter", str),
        version=_required(data, "version", str),
        config=dict(data.get("config", {})),
        metadata=dict(data.get("metadata", {})),
    )


def load_task(path: str | Path, verify_assets: bool = True) -> TaskSpec:
    path = Path(path).resolve()
    data = _read_json(path)
    verifier_data = _required(data, "verifier", dict)
    task = TaskSpec(
        schema_version=int(data.get("schema_version", 1)),
        id=_required(data, "id", str),
        title=_required(data, "title", str),
        instruction=_required(data, "instruction", str),
        environment_id=_required(data, "environment_id", str),
        inputs=tuple(InputArtifact(**item) for item in data.get("inputs", [])),
        outputs=tuple(OutputArtifact(**item) for item in data.get("outputs", [])),
        verifier=VerificationSpec(
            adapter=_required(verifier_data, "adapter", str),
            config=dict(verifier_data.get("config", {})),
        ),
        initial_state=dict(data.get("initial_state", {})),
        taxonomy=dict(data.get("taxonomy", {})),
        metadata=dict(data.get("metadata", {})),
        source_dir=path.parent,
    )
    if task.schema_version != 1:
        raise SpecError(f"unsupported task schema_version: {task.schema_version}")
    if verify_assets:
        _verify_assets(task)
    return task


def _verify_assets(task: TaskSpec) -> None:
    for artifact in task.inputs:
        source = (task.source_dir / artifact.source).resolve()
        if not source.is_relative_to(task.source_dir):
            raise SpecError(f"input source escapes task directory: {artifact.source}")
        if not source.is_file():
            raise SpecError(f"input source does not exist: {artifact.source}")
        if artifact.sha256:
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if digest != artifact.sha256:
                raise SpecError(f"sha256 mismatch for {artifact.source}")


def validate_pair(task: TaskSpec, environment: EnvironmentSpec) -> None:
    if task.environment_id != environment.id:
        raise SpecError(
            f"task requires environment {task.environment_id!r}, got {environment.id!r}"
        )
