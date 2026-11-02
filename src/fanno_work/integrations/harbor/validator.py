from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from fanno_work.tasking.hashing import (
    bundle_digest,
    sha256_file,
)


class HarborPackageError(ValueError):
    pass


_REQUIRED_FILES = {
    "task.toml",
    "instruction.md",
    "environment/Dockerfile",
    "environment/docker-compose.yaml",
    "environment/setup_task.py",
    "environment/input-manifest.json",
    "tests/test.sh",
    "tests/evaluate.py",
    "tests/bundle/manifest.json",
    "harbor-manifest.json",
}


def _read_json(
    path: Path,
) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ) as exc:
        raise HarborPackageError(
            f"cannot read {path}: {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise HarborPackageError(
            f"{path} must contain a JSON object"
        )

    return value


def _safe_path(
    root: Path,
    relative: str,
) -> Path:
    pure = PurePosixPath(relative)

    if (
        pure.is_absolute()
        or not pure.parts
        or ".." in pure.parts
    ):
        raise HarborPackageError(
            f"unsafe Harbor package path: "
            f"{relative!r}"
        )

    resolved = (
        root / pure
    ).resolve()

    if not resolved.is_relative_to(root):
        raise HarborPackageError(
            f"Harbor package path escapes root: "
            f"{relative!r}"
        )

    return resolved


def validate_harbor_package(
    root: str | Path,
) -> dict[str, Any]:
    resolved = Path(root).resolve()

    if not resolved.is_dir():
        raise HarborPackageError(
            "Harbor package directory "
            f"does not exist: {resolved}"
        )

    missing = sorted(
        relative
        for relative in _REQUIRED_FILES
        if not (
            resolved / relative
        ).is_file()
    )

    if missing:
        raise HarborPackageError(
            "Harbor package is missing files: "
            f"{missing}"
        )

    manifest = _read_json(
        resolved / "harbor-manifest.json"
    )

    if manifest.get("schema_version") != 1:
        raise HarborPackageError(
            "unsupported Harbor manifest "
            "schema_version"
        )

    raw_files = manifest.get("files")

    if not isinstance(raw_files, list):
        raise HarborPackageError(
            "Harbor manifest files must be a list"
        )

    declared: dict[
        str,
        dict[str, Any],
    ] = {}

    for item in raw_files:
        if not isinstance(item, dict):
            raise HarborPackageError(
                "Harbor manifest file entries "
                "must be objects"
            )

        relative = item.get("path")
        expected_sha256 = item.get("sha256")
        expected_size = item.get(
            "size_bytes"
        )

        if not isinstance(relative, str):
            raise HarborPackageError(
                "manifest file path must be "
                "a string"
            )

        if relative in declared:
            raise HarborPackageError(
                "duplicate Harbor manifest file: "
                f"{relative}"
            )

        path = _safe_path(
            resolved,
            relative,
        )

        if not path.is_file():
            raise HarborPackageError(
                "declared Harbor package file "
                f"is missing: {relative}"
            )

        if path.is_symlink():
            raise HarborPackageError(
                "Harbor package file must not "
                f"be a symlink: {relative}"
            )

        if (
            not isinstance(expected_size, int)
            or path.stat().st_size
            != expected_size
        ):
            raise HarborPackageError(
                f"size mismatch for {relative}"
            )

        if (
            not isinstance(
                expected_sha256,
                str,
            )
            or sha256_file(path)
            != expected_sha256
        ):
            raise HarborPackageError(
                f"sha256 mismatch for {relative}"
            )

        declared[relative] = item

    actual = {
        path.relative_to(
            resolved
        ).as_posix()
        for path in resolved.rglob("*")
        if path.is_file()
        and path.name
        != "harbor-manifest.json"
    }

    declared_paths = set(declared)

    if actual != declared_paths:
        raise HarborPackageError(
            "Harbor package inventory mismatch: "
            f"unregistered="
            f"{sorted(actual - declared_paths)}, "
            f"missing="
            f"{sorted(declared_paths - actual)}"
        )

    calculated_digest = bundle_digest(
        (
            relative,
            item["sha256"],
        )
        for relative, item
        in declared.items()
    )

    if calculated_digest != manifest.get(
        "package_digest"
    ):
        raise HarborPackageError(
            "Harbor package digest mismatch"
        )

    compose = (
        resolved
        / "environment"
        / "docker-compose.yaml"
    ).read_text(
        encoding="utf-8"
    )

    if "  main:" not in compose:
        raise HarborPackageError(
            "Harbor Compose environment must "
            "define the primary service 'main'"
        )

    instruction = (
        resolved / "instruction.md"
    ).read_text(
        encoding="utf-8"
    )

    if not instruction.strip():
        raise HarborPackageError(
            "instruction.md must not be empty"
        )

    test_script = (
        resolved / "tests" / "test.sh"
    )

    if not (
        test_script.stat().st_mode
        & 0o111
    ):
        raise HarborPackageError(
            "tests/test.sh must be executable"
        )

    forbidden_environment_files = {
        "environment/evaluator.json",
        "environment/bundle/evaluator.json",
        "environment/public/evaluator.json",
    }

    leaked = sorted(
        path
        for path in forbidden_environment_files
        if (resolved / path).exists()
    )

    if leaked:
        raise HarborPackageError(
            "Evaluator files leaked into "
            f"Agent environment: {leaked}"
        )

    return manifest