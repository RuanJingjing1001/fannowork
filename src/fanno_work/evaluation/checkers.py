from __future__ import annotations

import hashlib
from pathlib import Path
import time
from typing import Any
import xml.etree.ElementTree as ET
import zipfile

from .checker import CheckerRegistry
from .context import (
    EvaluationContext,
    EvaluationContextError,
)
from .models import (
    CheckSpec,
    CheckerResult,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(
                1024 * 1024
            ),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def _result(
    spec: CheckSpec,
    *,
    passed: bool,
    score: float,
    message: str,
    evidence: dict[str, Any],
    started: float,
    error: str | None = None,
) -> CheckerResult:
    return CheckerResult(
        id=spec.id,
        type=spec.type,
        component=spec.component,
        passed=passed,
        score=max(
            0.0,
            min(1.0, float(score)),
        ),
        weight=spec.weight,
        hard=spec.hard,
        message=message,
        evidence=evidence,
        error=error,
        duration_ms=(
            time.monotonic() - started
        )
        * 1000,
    )


def _required_string(
    spec: CheckSpec,
    key: str,
) -> str:
    value = spec.config.get(key)

    if not isinstance(value, str) or not value:
        raise ValueError(
            f"checker {spec.id} config "
            f"{key!r} must be a string"
        )

    return value


def _required_string_list(
    spec: CheckSpec,
    key: str,
    *,
    allow_empty: bool = False,
) -> list[str]:
    value = spec.config.get(key)

    if not isinstance(value, list) or not all(
        isinstance(item, str)
        and item
        for item in value
    ):
        raise ValueError(
            f"checker {spec.id} config "
            f"{key!r} must be a string list"
        )

    if not allow_empty and not value:
        raise ValueError(
            f"checker {spec.id} config "
            f"{key!r} must not be empty"
        )

    return list(value)


def _extract_docx_text(
    path: Path,
) -> tuple[str, dict[str, Any]]:
    if not zipfile.is_zipfile(path):
        raise ValueError(
            "file is not a valid ZIP-based DOCX"
        )

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())

        required = {
            "[Content_Types].xml",
            "word/document.xml",
        }

        missing = sorted(
            required - names
        )

        if missing:
            raise ValueError(
                "DOCX is missing required entries: "
                f"{missing}"
            )

        document_xml = archive.read(
            "word/document.xml"
        )

    root = ET.fromstring(document_xml)

    text_parts = [
        element.text
        for element in root.iter()
        if element.tag.endswith("}t")
        and isinstance(element.text, str)
    ]

    text = "\n".join(text_parts)

    return text, {
        "zip_entry_count": len(names),
        "document_xml_bytes": len(
            document_xml
        ),
        "character_count": len(text),
    }


class RequiredArtifactsChecker:
    def check(
        self,
        context: EvaluationContext,
        spec: CheckSpec,
    ) -> CheckerResult:
        started = time.monotonic()

        paths = _required_string_list(
            spec,
            "paths",
        )

        evidence: list[
            dict[str, Any]
        ] = []

        missing: list[str] = []

        for relative in paths:
            path = context.resolve_artifact(
                relative
            )

            exists = (
                path.is_file()
                and not path.is_symlink()
            )

            item: dict[str, Any] = {
                "path": relative,
                "exists": exists,
            }

            if exists:
                item.update(
                    {
                        "size_bytes": (
                            path.stat().st_size
                        ),
                        "sha256": _sha256(path),
                    }
                )
            else:
                missing.append(relative)

            evidence.append(item)

        passed = not missing

        return _result(
            spec,
            passed=passed,
            score=1.0 if passed else 0.0,
            message=(
                "all required artifacts exist"
                if passed
                else (
                    "missing required artifacts: "
                    f"{missing}"
                )
            ),
            evidence={
                "artifacts": evidence,
                "missing": missing,
            },
            started=started,
        )


class DocxOpenableChecker:
    def check(
        self,
        context: EvaluationContext,
        spec: CheckSpec,
    ) -> CheckerResult:
        started = time.monotonic()
        relative = _required_string(
            spec,
            "path",
        )
        path = context.resolve_artifact(
            relative
        )

        if not path.is_file():
            return _result(
                spec,
                passed=False,
                score=0.0,
                message=(
                    "DOCX artifact does not exist"
                ),
                evidence={
                    "path": relative,
                    "exists": False,
                },
                started=started,
            )

        try:
            text, details = (
                _extract_docx_text(path)
            )
        except (
            OSError,
            ValueError,
            zipfile.BadZipFile,
            ET.ParseError,
        ) as exc:
            return _result(
                spec,
                passed=False,
                score=0.0,
                message=(
                    "DOCX could not be opened "
                    "and parsed"
                ),
                evidence={
                    "path": relative,
                    "size_bytes": (
                        path.stat().st_size
                    ),
                    "sha256": _sha256(path),
                },
                error=(
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
                started=started,
            )

        passed = bool(text.strip())

        return _result(
            spec,
            passed=passed,
            score=1.0 if passed else 0.0,
            message=(
                "DOCX is structurally valid "
                "and contains text"
                if passed
                else "DOCX contains no text"
            ),
            evidence={
                "path": relative,
                "size_bytes": (
                    path.stat().st_size
                ),
                "sha256": _sha256(path),
                **details,
            },
            started=started,
        )


class DocumentContainsChecker:
    def check(
        self,
        context: EvaluationContext,
        spec: CheckSpec,
    ) -> CheckerResult:
        started = time.monotonic()
        relative = _required_string(
            spec,
            "path",
        )
        path = context.resolve_artifact(
            relative
        )

        all_terms = spec.config.get(
            "all_terms",
            [],
        )
        any_terms = spec.config.get(
            "any_terms",
            [],
        )
        min_characters = spec.config.get(
            "min_characters",
            1,
        )
        case_sensitive = spec.config.get(
            "case_sensitive",
            False,
        )

        if not isinstance(
            all_terms,
            list,
        ) or not all(
            isinstance(item, str)
            and item
            for item in all_terms
        ):
            raise ValueError(
                "all_terms must be a string list"
            )

        if not isinstance(
            any_terms,
            list,
        ) or not all(
            isinstance(item, str)
            and item
            for item in any_terms
        ):
            raise ValueError(
                "any_terms must be a string list"
            )

        if not isinstance(
            min_characters,
            int,
        ) or min_characters < 0:
            raise ValueError(
                "min_characters must be "
                "a non-negative integer"
            )

        if not isinstance(
            case_sensitive,
            bool,
        ):
            raise ValueError(
                "case_sensitive must be boolean"
            )

        if not path.is_file():
            return _result(
                spec,
                passed=False,
                score=0.0,
                message=(
                    "document artifact does "
                    "not exist"
                ),
                evidence={
                    "path": relative,
                    "exists": False,
                },
                started=started,
            )

        try:
            suffix = path.suffix.casefold()

            if suffix == ".docx":
                text, details = (
                    _extract_docx_text(path)
                )
            else:
                text = path.read_text(
                    encoding="utf-8"
                )
                details = {
                    "character_count": len(
                        text
                    )
                }
        except Exception as exc:
            return _result(
                spec,
                passed=False,
                score=0.0,
                message=(
                    "document text could not "
                    "be extracted"
                ),
                evidence={
                    "path": relative,
                    "sha256": _sha256(path),
                },
                error=(
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
                started=started,
            )

        searchable = (
            text
            if case_sensitive
            else text.casefold()
        )

        def normalize(term: str) -> str:
            return (
                term
                if case_sensitive
                else term.casefold()
            )

        found_all = [
            term
            for term in all_terms
            if normalize(term) in searchable
        ]
        missing_all = [
            term
            for term in all_terms
            if normalize(term)
            not in searchable
        ]
        found_any = [
            term
            for term in any_terms
            if normalize(term) in searchable
        ]

        length_passed = (
            len(text) >= min_characters
        )
        all_passed = not missing_all
        any_passed = (
            True
            if not any_terms
            else bool(found_any)
        )

        passed = (
            length_passed
            and all_passed
            and any_passed
        )

        requirements = (
            len(all_terms)
            + (1 if any_terms else 0)
            + 1
        )
        achieved = (
            len(found_all)
            + (1 if any_passed else 0)
            + (1 if length_passed else 0)
        )

        score = (
            achieved / requirements
            if requirements
            else 1.0
        )

        return _result(
            spec,
            passed=passed,
            score=score,
            message=(
                "document content requirements "
                "are satisfied"
                if passed
                else (
                    "document content requirements "
                    "are incomplete"
                )
            ),
            evidence={
                "path": relative,
                "sha256": _sha256(path),
                "character_count": len(text),
                "minimum_characters": (
                    min_characters
                ),
                "minimum_characters_passed": (
                    length_passed
                ),
                "found_all_terms": found_all,
                "missing_all_terms": (
                    missing_all
                ),
                "found_any_terms": found_any,
                **details,
            },
            started=started,
        )


class InputHashesUnchangedChecker:
    def check(
        self,
        context: EvaluationContext,
        spec: CheckSpec,
    ) -> CheckerResult:
        started = time.monotonic()

        raw_inputs = context.task.get(
            "inputs",
            [],
        )

        if not isinstance(raw_inputs, list):
            raise ValueError(
                "task inputs must be a list"
            )

        evidence: list[
            dict[str, Any]
        ] = []
        changed: list[str] = []

        for artifact in raw_inputs:
            if not isinstance(
                artifact,
                dict,
            ):
                raise ValueError(
                    "task input must be an object"
                )

            workspace_path = artifact.get(
                "path"
            )
            source_path = artifact.get(
                "source"
            )

            if not isinstance(
                workspace_path,
                str,
            ) or not isinstance(
                source_path,
                str,
            ):
                raise ValueError(
                    "task input path and source "
                    "must be strings"
                )

            original = (
                context.resolve_bundle_file(
                    source_path
                )
            )
            materialized = (
                context.resolve_workspace(
                    f"inputs/{workspace_path}"
                )
            )

            original_exists = (
                original.is_file()
            )
            materialized_exists = (
                materialized.is_file()
            )

            original_digest = (
                _sha256(original)
                if original_exists
                else None
            )
            materialized_digest = (
                _sha256(materialized)
                if materialized_exists
                else None
            )

            unchanged = (
                original_exists
                and materialized_exists
                and original_digest
                == materialized_digest
            )

            if not unchanged:
                changed.append(workspace_path)

            evidence.append(
                {
                    "path": workspace_path,
                    "source": source_path,
                    "original_exists": (
                        original_exists
                    ),
                    "workspace_exists": (
                        materialized_exists
                    ),
                    "original_sha256": (
                        original_digest
                    ),
                    "workspace_sha256": (
                        materialized_digest
                    ),
                    "unchanged": unchanged,
                }
            )

        passed = not changed

        return _result(
            spec,
            passed=passed,
            score=1.0 if passed else 0.0,
            message=(
                "all task inputs are unchanged"
                if passed
                else (
                    "task inputs were changed "
                    "or removed"
                )
            ),
            evidence={
                "inputs": evidence,
                "changed_or_missing": changed,
            },
            started=started,
        )


class SnapshotPathEqualsChecker:
    def check(
        self,
        context: EvaluationContext,
        spec: CheckSpec,
    ) -> CheckerResult:
        started = time.monotonic()

        snapshot_name = _required_string(
            spec,
            "snapshot",
        )
        path = _required_string_list(
            spec,
            "path",
        )

        if "expected" not in spec.config:
            raise ValueError(
                "snapshot_path_equals requires "
                "expected"
            )

        expected = spec.config["expected"]

        try:
            actual = context.snapshot_value(
                snapshot_name,
                path,
            )
        except EvaluationContextError as exc:
            return _result(
                spec,
                passed=False,
                score=0.0,
                message=(
                    "snapshot path could not "
                    "be evaluated"
                ),
                evidence={
                    "snapshot": snapshot_name,
                    "path": path,
                    "expected": expected,
                },
                error=(
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
                started=started,
            )

        passed = actual == expected

        return _result(
            spec,
            passed=passed,
            score=1.0 if passed else 0.0,
            message=(
                "snapshot value matches"
                if passed
                else "snapshot value differs"
            ),
            evidence={
                "snapshot": snapshot_name,
                "path": path,
                "expected": expected,
                "actual": actual,
            },
            started=started,
        )


class ResetMatchesInitialChecker:
    def check(
        self,
        context: EvaluationContext,
        spec: CheckSpec,
    ) -> CheckerResult:
        started = time.monotonic()

        if context.reset_snapshot is None:
            return _result(
                spec,
                passed=False,
                score=0.0,
                message=(
                    "reset snapshot is unavailable"
                ),
                evidence={
                    "reset_snapshot_available": (
                        False
                    )
                },
                started=started,
            )

        initial_digest = (
            context.initial_snapshot.get(
                "digest"
            )
        )
        reset_digest = (
            context.reset_snapshot.get(
                "digest"
            )
        )

        if (
            isinstance(initial_digest, str)
            and isinstance(reset_digest, str)
        ):
            passed = (
                initial_digest == reset_digest
            )
        else:
            passed = (
                context.initial_snapshot
                == context.reset_snapshot
            )

        return _result(
            spec,
            passed=passed,
            score=1.0 if passed else 0.0,
            message=(
                "reset restored initial state"
                if passed
                else (
                    "reset state differs from "
                    "initial state"
                )
            ),
            evidence={
                "initial_digest": (
                    initial_digest
                ),
                "reset_digest": reset_digest,
            },
            started=started,
        )


def default_checker_registry() -> (
    CheckerRegistry
):
    registry = CheckerRegistry()

    registry.register(
        "required_artifacts",
        RequiredArtifactsChecker(),
    )
    registry.register(
        "docx_openable",
        DocxOpenableChecker(),
    )
    registry.register(
        "document_contains",
        DocumentContainsChecker(),
    )
    registry.register(
        "input_hashes_unchanged",
        InputHashesUnchangedChecker(),
    )
    registry.register(
        "snapshot_path_equals",
        SnapshotPathEqualsChecker(),
    )
    registry.register(
        "reset_matches_initial",
        ResetMatchesInitialChecker(),
    )

    return registry