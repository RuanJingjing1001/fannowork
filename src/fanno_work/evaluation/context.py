from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from fanno_work.tasking import TaskBundle


class EvaluationContextError(ValueError):
    pass


def _resolve_confined(
    root: Path,
    relative: str,
) -> Path:
    pure = PurePosixPath(relative)

    if (
        pure.is_absolute()
        or not pure.parts
        or ".." in pure.parts
    ):
        raise EvaluationContextError(
            f"unsafe evaluation path: {relative!r}"
        )

    resolved_root = root.resolve()
    resolved = (
        resolved_root / pure
    ).resolve()

    if not resolved.is_relative_to(
        resolved_root
    ):
        raise EvaluationContextError(
            f"path escapes trusted root: "
            f"{relative!r}"
        )

    return resolved


@dataclass(frozen=True)
class EvaluationContext:
    task_id: str
    task: dict[str, Any]
    bundle_root: Path
    run_dir: Path
    initial_snapshot: dict[str, Any]
    final_snapshot: dict[str, Any]
    reset_snapshot: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    @classmethod
    def from_bundle(
        cls,
        bundle: TaskBundle,
        run_dir: str | Path,
        *,
        initial_snapshot: dict[str, Any],
        final_snapshot: dict[str, Any],
        reset_snapshot: (
            dict[str, Any] | None
        ) = None,
        metadata: (
            dict[str, Any] | None
        ) = None,
    ) -> EvaluationContext:
        return cls(
            task_id=bundle.manifest.bundle_id,
            task=dict(bundle.task),
            bundle_root=bundle.root.resolve(),
            run_dir=Path(run_dir).resolve(),
            initial_snapshot=dict(
                initial_snapshot
            ),
            final_snapshot=dict(
                final_snapshot
            ),
            reset_snapshot=(
                dict(reset_snapshot)
                if reset_snapshot is not None
                else None
            ),
            metadata=dict(metadata or {}),
        )

    @property
    def artifact_dir(self) -> Path:
        return self.run_dir / "artifacts"

    @property
    def workspace_dir(self) -> Path:
        return self.run_dir / "workspace"

    @property
    def evaluation_dir(self) -> Path:
        return self.run_dir / "evaluation"

    @property
    def audit_path(self) -> Path:
        return (
            self.run_dir
            / "audit"
            / "actions.jsonl"
        )

    @property
    def trajectory_path(self) -> Path:
        return (
            self.run_dir
            / "trajectory.jsonl"
        )

    def resolve_artifact(
        self,
        relative: str,
    ) -> Path:
        return _resolve_confined(
            self.artifact_dir,
            relative,
        )

    def resolve_workspace(
        self,
        relative: str,
    ) -> Path:
        return _resolve_confined(
            self.workspace_dir,
            relative,
        )

    def resolve_bundle_file(
        self,
        relative: str,
    ) -> Path:
        return _resolve_confined(
            self.bundle_root,
            relative,
        )

    def snapshot(
        self,
        name: str,
    ) -> dict[str, Any]:
        if name == "initial":
            return self.initial_snapshot

        if name == "final":
            return self.final_snapshot

        if name == "reset":
            if self.reset_snapshot is None:
                raise EvaluationContextError(
                    "reset snapshot is unavailable"
                )
            return self.reset_snapshot

        raise EvaluationContextError(
            f"unknown snapshot: {name!r}"
        )

    def snapshot_value(
        self,
        snapshot_name: str,
        path: list[str],
    ) -> Any:
        value: Any = self.snapshot(
            snapshot_name
        )

        for item in path:
            if not isinstance(value, dict):
                raise EvaluationContextError(
                    "snapshot path traverses "
                    "a non-object value"
                )

            if item not in value:
                raise EvaluationContextError(
                    "snapshot path does not exist: "
                    f"{path}"
                )

            value = value[item]

        return value