from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from typing import Any, Iterable

from fanno_work.specs import (
    load_environment,
    load_task,
    validate_pair,
)

from .bundle import TaskBundle, TaskBundleError, read_json_object
from .manifest import (
    BundleManifest,
    ImageReference,
    build_manifest_files,
    calculate_manifest_digest,
)
from .validator import validate_task_bundle


def _write_json(
    path: Path,
    value: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _safe_relative_asset(
    task_dir: Path,
    relative: str,
) -> tuple[str, Path]:
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or ".." in pure.parts
        or not pure.parts
    ):
        raise TaskBundleError(
            f"unsafe task asset path: {relative!r}"
        )

    if pure.parts[0] != "assets":
        raise TaskBundleError(
            "bundle assets must be under assets/: "
            f"{relative!r}"
        )

    source = (task_dir / pure).resolve()
    if not source.is_relative_to(task_dir):
        raise TaskBundleError(
            f"asset escapes task directory: {relative!r}"
        )
    if not source.is_file():
        raise TaskBundleError(
            f"referenced task asset does not exist: {relative!r}"
        )
    if source.is_symlink():
        raise TaskBundleError(
            f"task asset must not be a symlink: {relative!r}"
        )

    return pure.as_posix(), source


def _walk_asset_references(
    value: Any,
) -> Iterable[str]:
    if isinstance(value, str):
        normalized = value.replace("\\", "/")
        if (
            normalized.startswith("assets/")
            and normalized != "assets/"
            and not normalized.endswith("/")
        ):
            yield normalized
        return

    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_asset_references(
                item
            )
        return

    if isinstance(value, list):
        for item in value:
            yield from _walk_asset_references(
                item
            )


def _resolve_evaluator(
    task_data: dict[str, Any],
    task_dir: Path,
    explicit_path: Path | None,
) -> dict[str, Any]:
    path = explicit_path

    if path is None:
        metadata = task_data.get("metadata", {})
        if isinstance(metadata, dict):
            raw_path = metadata.get(
                "evaluator_file"
            )
            if isinstance(raw_path, str):
                path = task_dir / raw_path

    if path is not None:
        resolved = Path(path).resolve()
        if not resolved.is_file():
            raise TaskBundleError(
                f"evaluator file does not exist: {resolved}"
            )
        return read_json_object(resolved)

    verifier = task_data.get("verifier")
    if not isinstance(verifier, dict):
        raise TaskBundleError(
            "task verifier must be an object"
        )

    task_id = task_data.get("id")
    if not isinstance(task_id, str):
        raise TaskBundleError(
            "task id must be a string"
        )

    config = verifier.get("config", {})
    if not isinstance(config, dict):
        raise TaskBundleError(
            "task verifier config must be an object"
        )

    return {
        "schema_version": 1,
        "id": f"{task_id}.evaluator",
        "version": "0.1.0",
        "adapter": verifier.get("adapter"),
        "config": config,
        "status": config.get(
            "status",
            "task_declared",
        ),
        "provenance": "task.verifier",
    }


def _assert_safe_replacement(
    target: Path,
    expected_bundle_id: str,
) -> None:
    if target.is_symlink():
        raise TaskBundleError(
            "refusing to replace a symlink output directory: "
            f"{target}"
        )
    if not target.is_dir():
        raise TaskBundleError(
            "refusing to replace a non-directory output: "
            f"{target}"
        )
    if not any(target.iterdir()):
        return

    manifest_path = target / "manifest.json"
    if not manifest_path.is_file():
        raise TaskBundleError(
            "refusing to replace a non-bundle directory: "
            f"{target}"
        )

    manifest = read_json_object(manifest_path)
    if manifest.get("bundle_id") != expected_bundle_id:
        raise TaskBundleError(
            "refusing to replace a bundle with a different id: "
            f"{target}"
        )


class TaskBundleResolver:
    def build(
        self,
        *,
        task_path: str | Path,
        environment_path: str | Path,
        output_dir: str | Path,
        evaluator_path: str | Path | None = None,
        force: bool = False,
    ) -> TaskBundle:
        task_source = Path(task_path).resolve()
        environment_source = Path(
            environment_path
        ).resolve()
        target = Path(output_dir).resolve()

        task = load_task(
            task_source,
            verify_assets=True,
        )
        environment = load_environment(
            environment_source
        )
        validate_pair(task, environment)

        task_data = read_json_object(
            task_source
        )
        environment_data = read_json_object(
            environment_source
        )

        state_file = task_data.get(
            "initial_state_file"
        )
        if not isinstance(state_file, str):
            raise TaskBundleError(
                "canonical bundles require initial_state_file"
            )

        seed_source = (
            task_source.parent / state_file
        ).resolve()
        if not seed_source.is_relative_to(
            task_source.parent
        ):
            raise TaskBundleError(
                "initial_state_file escapes task directory"
            )
        seed_data = read_json_object(seed_source)

        actions_file = environment.config.get(
            "actions_file"
        )
        if not isinstance(actions_file, str):
            raise TaskBundleError(
                "environment config.actions_file must be a string"
            )
        actions_source = (
            environment_source.parent
            / actions_file
        ).resolve()
        if not actions_source.is_relative_to(
            environment_source.parent
        ):
            raise TaskBundleError(
                "actions_file escapes environment directory"
            )
        actions_data = read_json_object(
            actions_source
        )

        metadata = task_data.get("metadata", {})
        provenance_file = (
            metadata.get("provenance_file")
            if isinstance(metadata, dict)
            else None
        )
        if isinstance(provenance_file, str):
            provenance_source = (
                task_source.parent
                / provenance_file
            ).resolve()
            if not provenance_source.is_relative_to(
                task_source.parent
            ):
                raise TaskBundleError(
                    "provenance_file escapes task directory"
                )
            provenance_data = read_json_object(
                provenance_source
            )
        else:
            provenance_data = {
                "schema_version": 1,
                "package_id": task.id,
                "source": {
                    "status": "not_specified"
                },
            }

        evaluator_data = _resolve_evaluator(
            task_data,
            task_source.parent,
            (
                Path(evaluator_path)
                if evaluator_path is not None
                else None
            ),
        )

        asset_references = {
            artifact.source
            for artifact in task.inputs
        }
        asset_references.update(
            _walk_asset_references(seed_data)
        )
        asset_references.update(
            _walk_asset_references(
                provenance_data
            )
        )

        resolved_assets = dict(
            _safe_relative_asset(
                task_source.parent,
                reference,
            )
            for reference in sorted(
                asset_references
            )
        )

        if target.exists() and not force:
            raise TaskBundleError(
                f"bundle output already exists: {target}"
            )
        if target.exists():
            _assert_safe_replacement(
                target,
                task.id,
            )

        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        temporary = Path(
            tempfile.mkdtemp(
                prefix=f".{target.name}.",
                dir=target.parent,
            )
        )

        roles = {
            "task.json": "task_spec",
            "environment.json": (
                "environment_profile"
            ),
            "actions.json": "action_catalog",
            "seed.json": "initial_state",
            "evaluator.json": "evaluator_spec",
            "provenance.json": "provenance",
        }

        try:
            canonical_task = dict(task_data)
            canonical_task[
                "initial_state_file"
            ] = "seed.json"

            canonical_environment = dict(
                environment_data
            )
            canonical_config = dict(
                canonical_environment.get(
                    "config",
                    {},
                )
            )
            canonical_config[
                "actions_file"
            ] = "actions.json"
            canonical_environment[
                "config"
            ] = canonical_config

            _write_json(
                temporary / "task.json",
                canonical_task,
            )
            _write_json(
                temporary / "environment.json",
                canonical_environment,
            )
            _write_json(
                temporary / "actions.json",
                actions_data,
            )
            _write_json(
                temporary / "seed.json",
                seed_data,
            )
            _write_json(
                temporary / "evaluator.json",
                evaluator_data,
            )
            _write_json(
                temporary / "provenance.json",
                provenance_data,
            )

            for relative, source in (
                resolved_assets.items()
            ):
                destination = temporary / relative
                destination.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                shutil.copy2(source, destination)
                roles[relative] = "task_asset"

            files = build_manifest_files(
                temporary,
                roles,
            )

            runtime_image = (
                environment.config.get(
                    "runtime_image"
                )
            )
            images = ()
            if isinstance(runtime_image, str):
                image_reference = runtime_image
                image_digest = None
                if "@sha256:" in runtime_image:
                    (
                        image_reference,
                        raw_digest,
                    ) = runtime_image.split("@", 1)
                    image_digest = raw_digest
                images = (
                    ImageReference(
                        reference=image_reference,
                        digest=image_digest,
                        provenance=(
                            "pinned_profile_reference"
                            if image_digest
                            else "unpinned_profile_reference"
                        ),
                    ),
                )

            evaluator_id = evaluator_data.get(
                "id"
            )
            if not isinstance(evaluator_id, str):
                raise TaskBundleError(
                    "evaluator id must be a string"
                )

            manifest = BundleManifest(
                schema_version=1,
                bundle_id=task.id,
                task={
                    "id": task.id,
                    "schema_version": (
                        task.schema_version
                    ),
                },
                environment={
                    "id": environment.id,
                    "version": environment.version,
                    "adapter": environment.adapter,
                },
                action_catalog={
                    "schema_version": (
                        actions_data.get(
                            "schema_version"
                        )
                    ),
                    "action_count": len(
                        actions_data.get(
                            "actions",
                            [],
                        )
                    ),
                },
                evaluator={
                    "id": evaluator_id,
                    "version": evaluator_data.get(
                        "version"
                    ),
                    "adapter": evaluator_data.get(
                        "adapter"
                    ),
                    "status": evaluator_data.get(
                        "status"
                    ),
                },
                files=files,
                images=images,
                bundle_digest=(
                    calculate_manifest_digest(
                        files
                    )
                ),
                metadata={
                    "deterministic": True,
                    "source_task_filename": (
                        task_source.name
                    ),
                    "source_environment_filename": (
                        environment_source.name
                    ),
                },
            )

            _write_json(
                temporary / "manifest.json",
                manifest.to_dict(),
            )

            validate_task_bundle(temporary)

            if target.exists():
                shutil.rmtree(target)
            os.replace(temporary, target)

        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise

        validate_task_bundle(target)
        return TaskBundle.load(target)
