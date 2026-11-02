from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from .bundle import TaskBundleError, read_json_object
from .hashing import sha256_file
from .manifest import (
    BundleManifest,
    ImageReference,
    ManifestFile,
    calculate_manifest_digest,
)


_REQUIRED_FILES = {
    "task.json",
    "environment.json",
    "actions.json",
    "seed.json",
    "evaluator.json",
    "provenance.json",
    "manifest.json",
}


def _required(
    value: dict[str, Any],
    key: str,
    expected: type,
) -> Any:
    item = value.get(key)
    if not isinstance(item, expected):
        raise TaskBundleError(
            f"manifest field {key!r} must be "
            f"{expected.__name__}"
        )
    return item


def parse_manifest(
    value: dict[str, Any],
) -> BundleManifest:
    raw_files = _required(
        value,
        "files",
        list,
    )
    raw_images = value.get("images", [])

    if not isinstance(raw_images, list):
        raise TaskBundleError(
            "manifest field 'images' must be list"
        )

    try:
        manifest_files = tuple(
            ManifestFile(**item)
            for item in raw_files
        )
        images = tuple(
            ImageReference(**item)
            for item in raw_images
        )
    except (TypeError, ValueError) as exc:
        raise TaskBundleError(
            f"invalid manifest entry: {exc}"
        ) from exc

    return BundleManifest(
        schema_version=int(
            value.get("schema_version", 0)
        ),
        bundle_id=_required(
            value,
            "bundle_id",
            str,
        ),
        task=_required(value, "task", dict),
        environment=_required(
            value,
            "environment",
            dict,
        ),
        action_catalog=_required(
            value,
            "action_catalog",
            dict,
        ),
        evaluator=_required(
            value,
            "evaluator",
            dict,
        ),
        files=manifest_files,
        images=images,
        bundle_digest=_required(
            value,
            "bundle_digest",
            str,
        ),
        metadata=dict(
            value.get("metadata", {})
        ),
    )


def _safe_bundle_path(
    root: Path,
    relative: str,
) -> Path:
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or ".." in pure.parts
        or not pure.parts
    ):
        raise TaskBundleError(
            f"unsafe manifest path: {relative!r}"
        )

    resolved = (root / pure).resolve()
    if not resolved.is_relative_to(root):
        raise TaskBundleError(
            f"manifest path escapes bundle: {relative!r}"
        )
    return resolved


def validate_task_bundle(
    root: str | Path,
) -> BundleManifest:
    resolved = Path(root).resolve()

    if not resolved.is_dir():
        raise TaskBundleError(
            f"bundle directory does not exist: {resolved}"
        )

    missing = sorted(
        name
        for name in _REQUIRED_FILES
        if not (resolved / name).is_file()
    )
    if missing:
        raise TaskBundleError(
            f"bundle is missing required files: {missing}"
        )

    manifest = parse_manifest(
        read_json_object(
            resolved / "manifest.json"
        )
    )

    if manifest.schema_version != 1:
        raise TaskBundleError(
            "unsupported bundle manifest schema_version: "
            f"{manifest.schema_version}"
        )

    seen: set[str] = set()
    for item in manifest.files:
        if item.path in seen:
            raise TaskBundleError(
                f"duplicate manifest file: {item.path}"
            )
        seen.add(item.path)

        path = _safe_bundle_path(
            resolved,
            item.path,
        )
        if not path.is_file():
            raise TaskBundleError(
                f"manifest file is missing: {item.path}"
            )
        if path.is_symlink():
            raise TaskBundleError(
                f"bundle file must not be a symlink: {item.path}"
            )
        if path.stat().st_size != item.size_bytes:
            raise TaskBundleError(
                f"size mismatch for {item.path}"
            )
        if sha256_file(path) != item.sha256:
            raise TaskBundleError(
                f"sha256 mismatch for {item.path}"
            )

    actual_files = {
        path.relative_to(resolved).as_posix()
        for path in resolved.rglob("*")
        if path.is_file()
        and path.relative_to(resolved).as_posix()
        != "manifest.json"
    }
    if actual_files != seen:
        unregistered = sorted(
            actual_files - seen
        )
        missing_from_disk = sorted(
            seen - actual_files
        )
        raise TaskBundleError(
            "bundle file inventory mismatch: "
            f"unregistered={unregistered}, "
            f"missing={missing_from_disk}"
        )

    calculated = calculate_manifest_digest(
        manifest.files
    )
    if calculated != manifest.bundle_digest:
        raise TaskBundleError(
            "bundle digest mismatch"
        )

    task = read_json_object(
        resolved / "task.json"
    )
    environment = read_json_object(
        resolved / "environment.json"
    )
    actions = read_json_object(
        resolved / "actions.json"
    )
    evaluator = read_json_object(
        resolved / "evaluator.json"
    )

    if task.get("id") != manifest.bundle_id:
        raise TaskBundleError(
            "task id does not match bundle_id"
        )
    if task.get("environment_id") != environment.get("id"):
        raise TaskBundleError(
            "task environment_id does not match environment id"
        )
    if manifest.task.get("id") != task.get("id"):
        raise TaskBundleError(
            "manifest task identity mismatch"
        )
    if manifest.environment.get("id") != environment.get("id"):
        raise TaskBundleError(
            "manifest environment identity mismatch"
        )
    if manifest.action_catalog.get("schema_version") != actions.get(
        "schema_version"
    ):
        raise TaskBundleError(
            "manifest action catalog version mismatch"
        )
    if manifest.evaluator.get("id") != evaluator.get("id"):
        raise TaskBundleError(
            "manifest evaluator identity mismatch"
        )

    raw_actions = actions.get("actions")
    if not isinstance(raw_actions, list):
        raise TaskBundleError(
            "action catalog actions must be a list"
        )

    action_ids: list[str] = []
    for action in raw_actions:
        if not isinstance(action, dict):
            raise TaskBundleError(
                "action catalog entries must be objects"
            )
        action_id = action.get("id")
        if not isinstance(action_id, str) or not action_id:
            raise TaskBundleError(
                "action id must be a non-empty string"
            )
        action_ids.append(action_id)

    if len(action_ids) != len(set(action_ids)):
        raise TaskBundleError(
            "action catalog contains duplicate ids"
        )

    environment_config = environment.get(
        "config",
        {},
    )
    if not isinstance(environment_config, dict):
        raise TaskBundleError(
            "environment config must be an object"
        )
    enabled_actions = environment_config.get(
        "enabled_actions"
    )
    if enabled_actions is not None:
        if not isinstance(enabled_actions, list) or not all(
            isinstance(item, str)
            for item in enabled_actions
        ):
            raise TaskBundleError(
                "environment enabled_actions must be a string list"
            )
        undeclared = sorted(
            set(enabled_actions) - set(action_ids)
        )
        if undeclared:
            raise TaskBundleError(
                "environment enables actions missing from catalog: "
                f"{undeclared}"
            )

    evaluator_adapter = evaluator.get("adapter")
    if not isinstance(evaluator_adapter, str) or not evaluator_adapter:
        raise TaskBundleError(
            "evaluator adapter must be a non-empty string"
        )

    declared_assets = {
        item.path
        for item in manifest.files
        if item.role == "task_asset"
    }
    for artifact in task.get("inputs", []):
        if not isinstance(artifact, dict):
            raise TaskBundleError(
                "task inputs must contain objects"
            )
        source = artifact.get("source")
        if not isinstance(source, str):
            raise TaskBundleError(
                "task input source must be a string"
            )
        if source not in declared_assets:
            raise TaskBundleError(
                f"task input is not in manifest assets: {source}"
            )

    return manifest
