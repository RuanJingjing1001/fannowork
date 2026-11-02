from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .manifest import BundleManifest


class TaskBundleError(ValueError):
    pass


def read_json_object(
    path: Path,
) -> dict[str, Any]:
    try:
        value = json.loads(
            Path(path).read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskBundleError(
            f"cannot read JSON object {path}: {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise TaskBundleError(
            f"{path} must contain a JSON object"
        )

    return value


@dataclass(frozen=True)
class TaskBundle:
    root: Path
    task: dict[str, Any]
    environment: dict[str, Any]
    actions: dict[str, Any]
    seed: dict[str, Any]
    evaluator: dict[str, Any]
    provenance: dict[str, Any]
    manifest: BundleManifest

    @classmethod
    def load(
        cls,
        root: str | Path,
    ) -> TaskBundle:
        resolved = Path(root).resolve()

        manifest_data = read_json_object(
            resolved / "manifest.json"
        )

        from .validator import parse_manifest

        manifest = parse_manifest(
            manifest_data
        )

        return cls(
            root=resolved,
            task=read_json_object(
                resolved / "task.json"
            ),
            environment=read_json_object(
                resolved / "environment.json"
            ),
            actions=read_json_object(
                resolved / "actions.json"
            ),
            seed=read_json_object(
                resolved / "seed.json"
            ),
            evaluator=read_json_object(
                resolved / "evaluator.json"
            ),
            provenance=read_json_object(
                resolved / "provenance.json"
            ),
            manifest=manifest,
        )
