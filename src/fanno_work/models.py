from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


JsonObject = dict[str, Any]


@dataclass(frozen=True)
class InputArtifact:
    path: str
    source: str
    media_type: str = "application/octet-stream"
    sha256: str | None = None


@dataclass(frozen=True)
class OutputArtifact:
    path: str
    media_type: str = "application/octet-stream"
    required: bool = True


@dataclass(frozen=True)
class VerificationSpec:
    adapter: str
    config: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class TaskSpec:
    schema_version: int
    id: str
    title: str
    instruction: str
    environment_id: str
    inputs: tuple[InputArtifact, ...]
    outputs: tuple[OutputArtifact, ...]
    verifier: VerificationSpec
    initial_state: JsonObject = field(default_factory=dict)
    taxonomy: JsonObject = field(default_factory=dict)
    metadata: JsonObject = field(default_factory=dict)
    source_dir: Path = field(default=Path("."), repr=False, compare=False)


@dataclass(frozen=True)
class EnvironmentSpec:
    schema_version: int
    id: str
    adapter: str
    version: str
    config: JsonObject = field(default_factory=dict)
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class ActionDescriptor:
    name: str
    description: str
    input_schema: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    task_id: str
    state_version: int
    available_actions: tuple[ActionDescriptor, ...]
    data: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class ActionRequest:
    name: str
    arguments: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    output: JsonObject = field(default_factory=dict)
    error: str | None = None
    state_changed: bool = False


@dataclass(frozen=True)
class CheckResult:
    id: str
    passed: bool
    message: str
    evidence: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    score: float
    checks: tuple[CheckResult, ...]
    metadata: JsonObject = field(default_factory=dict)


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value
