from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable

from fanno_work.models import (
    ActionRequest,
    ActionResult,
)

from fanno_legal.backends.document.libreoffice_uno import (
    LibreOfficeEdit,
    LibreOfficeUnoBackend,
    LibreOfficeUnoFailure,
)
from fanno_legal.capabilities.filesystem import (
    FilesystemCapability,
    FilesystemFailure,
)
from fanno_legal.state.audit import AuditLog
from fanno_legal.state.database import (
    StateDatabase,
)


class WriterFailure(Exception):
    def __init__(
        self,
        code: str,
    ) -> None:
        super().__init__(code)
        self.code = code


BackendCall = Callable[
    [Path | None, Path],
    LibreOfficeEdit,
]


class WriterCapability:
    ACTION_NAMES = frozenset(
        {
            "document.create",
            "document.insert_heading",
            "document.insert_paragraph",
            "document.add_table",
            "document.save_as",
        }
    )

    def __init__(
        self,
        filesystem: FilesystemCapability,
        database: StateDatabase,
        audit: AuditLog,
        config: dict[str, Any],
        backend: (
            LibreOfficeUnoBackend | None
        ) = None,
    ) -> None:
        self.filesystem = filesystem
        self.database = database
        self.audit = audit

        uno = config.get("uno")

        if not isinstance(uno, dict):
            raise ValueError(
                "document.uno must be an object"
            )

        self.input_extensions = (
            self._extension_set(
                uno.get(
                    "input_extensions"
                )
            )
        )
        self.output_extensions = (
            self._extension_set(
                uno.get(
                    "output_extensions"
                )
            )
        )

        self.max_file_bytes = (
            self._positive_integer(
                config.get(
                    "max_file_bytes"
                ),
                "document.max_file_bytes",
            )
        )
        self.max_output_bytes = (
            self._positive_integer(
                uno.get(
                    "max_output_bytes"
                ),
                (
                    "document.uno."
                    "max_output_bytes"
                ),
            )
        )
        self.max_insert_characters = (
            self._positive_integer(
                uno.get(
                    "max_insert_characters"
                ),
                (
                    "document.uno."
                    "max_insert_characters"
                ),
            )
        )
        self.max_table_rows = (
            self._positive_integer(
                uno.get(
                    "max_table_rows",
                    200,
                ),
                (
                    "document.uno."
                    "max_table_rows"
                ),
            )
        )
        self.max_table_columns = (
            self._positive_integer(
                uno.get(
                    "max_table_columns",
                    20,
                ),
                (
                    "document.uno."
                    "max_table_columns"
                ),
            )
        )

        if backend is None:
            self.backend = (
                LibreOfficeUnoBackend(
                    system_python=uno.get(
                        "system_python"
                    ),
                    soffice_executable=(
                        uno.get(
                            "soffice_executable"
                        )
                    ),
                    timeout_seconds=(
                        uno.get(
                            "timeout_seconds"
                        )
                    ),
                )
            )
        else:
            self.backend = backend

    def act(
        self,
        actor: str,
        request: ActionRequest,
    ) -> ActionResult:
        version_before = (
            self.database.get_state_version()
        )

        try:
            result = self._dispatch(
                request
            )
        except WriterFailure as exc:
            result = ActionResult(
                ok=False,
                error=exc.code,
            )

        version_after = (
            self.database.get_state_version()
        )

        self.audit.append(
            actor=actor,
            action=request.name,
            arguments=self._audit_arguments(
                request
            ),
            ok=result.ok,
            error=result.error,
            state_version_before=(
                version_before
            ),
            state_version_after=(
                version_after
            ),
        )

        return result

    def _dispatch(
        self,
        request: ActionRequest,
    ) -> ActionResult:
        if request.name == "document.create":
            return self._create(
                request.arguments
            )

        if (
            request.name
            == "document.insert_heading"
        ):
            return self._insert_heading(
                request.arguments
            )

        if (
            request.name
            == "document.insert_paragraph"
        ):
            return self._insert_paragraph(
                request.arguments
            )

        if request.name == "document.add_table":
            return self._add_table(
                request.arguments
            )

        if request.name == "document.save_as":
            return self._save_as(
                request.arguments
            )

        return ActionResult(
            ok=False,
            error="unknown_action",
        )

    def _create(
        self,
        arguments: dict[str, Any],
    ) -> ActionResult:
        path = arguments.get("path")
        title = arguments.get("title")

        if title is not None and (
            not isinstance(title, str)
            or len(title)
            > self.max_insert_characters
        ):
            raise WriterFailure(
                "invalid_text"
            )

        return self._execute(
            source_value=None,
            target_value=path,
            backend_call=(
                lambda _source, target:
                self.backend.create_document(
                    target=target,
                    title=title,
                )
            ),
        )

    def _insert_heading(
        self,
        arguments: dict[str, Any],
    ) -> ActionResult:
        path = arguments.get("path")
        text = self._validate_text(
            arguments.get("text")
        )
        level = arguments.get("level")

        if (
            not isinstance(level, int)
            or isinstance(level, bool)
            or level < 1
            or level > 10
        ):
            raise WriterFailure(
                "invalid_heading_level"
            )

        return self._execute(
            source_value=path,
            target_value=path,
            backend_call=(
                lambda source, target:
                self.backend.insert_heading(
                    source=self._require_source(
                        source
                    ),
                    target=target,
                    text=text,
                    level=level,
                )
            ),
        )

    def _insert_paragraph(
        self,
        arguments: dict[str, Any],
    ) -> ActionResult:
        path = arguments.get("path")
        text = self._validate_text(
            arguments.get("text")
        )
        style = arguments.get("style")

        if style is not None and (
            not isinstance(style, str)
            or not style.strip()
            or len(style) > 100
        ):
            raise WriterFailure(
                "invalid_style"
            )

        return self._execute(
            source_value=path,
            target_value=path,
            backend_call=(
                lambda source, target:
                self.backend.insert_paragraph(
                    source=self._require_source(
                        source
                    ),
                    target=target,
                    text=text,
                    style=style,
                )
            ),
        )

    def _add_table(
        self,
        arguments: dict[str, Any],
    ) -> ActionResult:
        path = arguments.get("path")
        headers = arguments.get("headers")
        rows = arguments.get("rows")

        if (
            not isinstance(headers, list)
            or not headers
            or not all(
                isinstance(item, str)
                and bool(item.strip())
                for item in headers
            )
        ):
            raise WriterFailure(
                "invalid_table"
            )

        if (
            len(headers)
            > self.max_table_columns
        ):
            raise WriterFailure(
                "table_too_large"
            )

        if not isinstance(rows, list):
            raise WriterFailure(
                "invalid_table"
            )

        if len(rows) > self.max_table_rows:
            raise WriterFailure(
                "table_too_large"
            )

        normalized_rows: list[
            list[str]
        ] = []

        for row in rows:
            if (
                not isinstance(row, list)
                or len(row) != len(headers)
                or not all(
                    isinstance(cell, str)
                    for cell in row
                )
            ):
                raise WriterFailure(
                    "invalid_table"
                )

            normalized_rows.append(
                list(row)
            )

        character_count = sum(
            len(value)
            for value in headers
        ) + sum(
            len(value)
            for row in normalized_rows
            for value in row
        )

        if (
            character_count
            > self.max_insert_characters
        ):
            raise WriterFailure(
                "text_too_large"
            )

        return self._execute(
            source_value=path,
            target_value=path,
            backend_call=(
                lambda source, target:
                self.backend.add_table(
                    source=self._require_source(
                        source
                    ),
                    target=target,
                    headers=list(headers),
                    rows=normalized_rows,
                )
            ),
        )

    def _save_as(
        self,
        arguments: dict[str, Any],
    ) -> ActionResult:
        source_path = arguments.get(
            "source_path"
        )
        target_path = arguments.get(
            "target_path"
        )

        if source_path == target_path:
            raise WriterFailure(
                "invalid_path"
            )

        return self._execute(
            source_value=source_path,
            target_value=target_path,
            backend_call=(
                lambda source, target:
                self.backend.save_as(
                    source=self._require_source(
                        source
                    ),
                    target=target,
                )
            ),
        )

    def _execute(
        self,
        *,
        source_value: Any,
        target_value: Any,
        backend_call: BackendCall,
    ) -> ActionResult:
        source_relative: str | None = None
        source_path: Path | None = None
        source_sha256: str | None = None

        try:
            if source_value is not None:
                (
                    source_relative,
                    source_path,
                ) = (
                    self.filesystem
                    .resolve_readable_file(
                        source_value
                    )
                )

            (
                target_relative,
                target_path,
            ) = (
                self.filesystem
                .resolve_writable_path(
                    target_value
                )
            )
        except FilesystemFailure as exc:
            raise WriterFailure(
                exc.code
            ) from exc

        if source_path is not None:
            if (
                source_path.suffix.lower()
                not in self.input_extensions
            ):
                raise WriterFailure(
                    "unsupported_format"
                )

            try:
                source_bytes = (
                    source_path.read_bytes()
                )
            except OSError as exc:
                raise WriterFailure(
                    "document_open_failed"
                ) from exc

            if (
                len(source_bytes)
                > self.max_file_bytes
            ):
                raise WriterFailure(
                    "file_too_large"
                )

            source_sha256 = (
                hashlib.sha256(
                    source_bytes
                ).hexdigest()
            )

        if (
            target_path.suffix.lower()
            not in self.output_extensions
        ):
            raise WriterFailure(
                "invalid_target_extension"
            )

        try:
            previous_bytes = (
                target_path.read_bytes()
                if target_path.is_file()
                else None
            )
        except OSError as exc:
            raise WriterFailure(
                "document_save_failed"
            ) from exc

        work_root = (
            self.filesystem.workspace
            / "work"
        )

        try:
            work_root.mkdir(
                parents=True,
                exist_ok=True,
            )

            with tempfile.TemporaryDirectory(
                prefix=".writer-uno-",
                dir=str(work_root),
            ) as directory:
                temporary_root = Path(
                    directory
                )
                input_root = (
                    temporary_root / "input"
                )
                output_root = (
                    temporary_root / "output"
                )

                input_root.mkdir()
                output_root.mkdir()

                staged_source: (
                    Path | None
                ) = None

                if source_path is not None:
                    staged_source = (
                        input_root
                        / source_path.name
                    )

                    shutil.copy2(
                        source_path,
                        staged_source,
                    )

                staged_target = (
                    output_root
                    / target_path.name
                )

                try:
                    edit = backend_call(
                        staged_source,
                        staged_target,
                    )
                except (
                    LibreOfficeUnoFailure
                ) as exc:
                    raise WriterFailure(
                        exc.code
                    ) from exc

                generated = (
                    edit.generated_path
                    .resolve()
                )

                if (
                    generated
                    != staged_target.resolve()
                    or not generated.is_file()
                    or not generated.is_relative_to(
                        output_root.resolve()
                    )
                ):
                    raise WriterFailure(
                        "edit_output_mismatch"
                    )

                generated_bytes = (
                    generated.read_bytes()
                )

                if not generated_bytes:
                    raise WriterFailure(
                        "edit_output_missing"
                    )

                if (
                    len(generated_bytes)
                    > self.max_output_bytes
                ):
                    raise WriterFailure(
                        "output_too_large"
                    )

                generated_sha256 = (
                    hashlib.sha256(
                        generated_bytes
                    ).hexdigest()
                )

                if (
                    edit.size
                    != len(generated_bytes)
                    or edit.sha256
                    != generated_sha256
                ):
                    raise WriterFailure(
                        "edit_output_mismatch"
                    )

                changed = (
                    generated_bytes
                    != previous_bytes
                )

                if changed:
                    target_path.parent.mkdir(
                        parents=True,
                        exist_ok=True,
                    )

                    generated.replace(
                        target_path
                    )

                    self.database.advance_state_version()

        except WriterFailure:
            raise
        except OSError as exc:
            raise WriterFailure(
                "document_edit_failed"
            ) from exc

        return ActionResult(
            ok=True,
            output={
                "source_path": (
                    source_relative
                ),
                "target_path": (
                    target_relative
                ),
                "source_sha256": (
                    source_sha256
                ),
                "size": len(
                    generated_bytes
                ),
                "sha256": (
                    generated_sha256
                ),
                "created": (
                    previous_bytes is None
                ),
                "backend": edit.backend,
                "backend_version": (
                    edit.backend_version
                ),
                "execution_evidence": dict(
                    edit.execution_evidence
                ),
            },
            state_changed=changed,
        )

    def _validate_text(
        self,
        value: Any,
    ) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
        ):
            raise WriterFailure(
                "invalid_text"
            )

        if (
            len(value)
            > self.max_insert_characters
        ):
            raise WriterFailure(
                "text_too_large"
            )

        return value

    @staticmethod
    def _require_source(
        value: Path | None,
    ) -> Path:
        if value is None:
            raise WriterFailure(
                "path_not_found"
            )

        return value

    @staticmethod
    def _positive_integer(
        value: Any,
        name: str,
    ) -> int:
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value <= 0
        ):
            raise ValueError(
                f"{name} must be a positive integer"
            )

        return value

    @staticmethod
    def _extension_set(
        value: Any,
    ) -> frozenset[str]:
        if (
            not isinstance(value, list)
            or not value
            or not all(
                isinstance(item, str)
                and item.startswith(".")
                for item in value
            )
        ):
            raise ValueError(
                "UNO extensions must be "
                "a non-empty extension list"
            )

        return frozenset(
            item.lower()
            for item in value
        )

    @staticmethod
    def _audit_arguments(
        request: ActionRequest,
    ) -> dict[str, Any]:
        arguments = dict(
            request.arguments
        )

        text = arguments.pop(
            "text",
            None,
        )

        if isinstance(text, str):
            encoded = text.encode("utf-8")
            arguments["text_sha256"] = (
                hashlib.sha256(
                    encoded
                ).hexdigest()
            )
            arguments["text_bytes"] = len(
                encoded
            )

        headers = arguments.pop(
            "headers",
            None,
        )
        rows = arguments.pop(
            "rows",
            None,
        )

        if headers is not None or rows is not None:
            encoded = json.dumps(
                {
                    "headers": headers,
                    "rows": rows,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")

            arguments["table_sha256"] = (
                hashlib.sha256(
                    encoded
                ).hexdigest()
            )
            arguments["table_bytes"] = len(
                encoded
            )
            arguments["table_rows"] = (
                len(rows)
                if isinstance(rows, list)
                else None
            )
            arguments["table_columns"] = (
                len(headers)
                if isinstance(
                    headers,
                    list,
                )
                else None
            )

        return arguments
