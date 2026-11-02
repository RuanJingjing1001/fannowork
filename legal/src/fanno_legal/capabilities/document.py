from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import tempfile
from typing import Any

from fanno_work.models import (
    ActionRequest,
    ActionResult,
)

from fanno_legal.backends.document.libreoffice_cli import (
    LibreOfficeBackend,
    LibreOfficeConversion,
    LibreOfficeFailure,
)
from fanno_legal.backends.document.libreoffice_uno import (
    LibreOfficeEdit,
    LibreOfficeUnoBackend,
    LibreOfficeUnoFailure,
)
from fanno_legal.backends.document.text_extractor import (
    TextExtractionFailure,
    TextExtractor,
)
from fanno_legal.capabilities.filesystem import (
    FilesystemCapability,
    FilesystemFailure,
)
from fanno_legal.state.audit import AuditLog
from fanno_legal.state.database import (
    StateDatabase,
)


class DocumentFailure(Exception):
    def __init__(
        self,
        code: str,
    ) -> None:
        super().__init__(code)
        self.code = code


class DocumentCapability:
    ACTION_NAMES = frozenset(
        {
            "document.extract_text",
            "document.convert",
            "document.append_paragraph",
        }
    )

    _MEDIA_TYPES = {
        ".txt": "text/plain",
        ".docx": (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        ".pdf": "application/pdf",
    }

    def __init__(
        self,
        filesystem: FilesystemCapability,
        database: StateDatabase,
        audit: AuditLog,
        config: dict[str, Any],
        libreoffice_backend: (
            LibreOfficeBackend | None
        ) = None,
        uno_backend: (
            LibreOfficeUnoBackend | None
        ) = None,
        text_extractor: TextExtractor | None = None,
    ) -> None:
        self.filesystem = filesystem
        self.database = database
        self.audit = audit

        self.supported_extensions = (
            self._extension_set(
                config,
                "supported_extensions",
                "document",
            )
        )

        self.max_file_bytes = (
            self._positive_integer(
                config,
                "max_file_bytes",
                "document",
            )
        )
        self.max_text_characters = (
            self._positive_integer(
                config,
                "max_text_characters",
                "document",
            )
        )
        self.max_archive_uncompressed_bytes = (
            self._positive_integer(
                config,
                "max_archive_uncompressed_bytes",
                "document",
            )
        )
        self.text_extractor = (
            text_extractor
            if text_extractor is not None
            else TextExtractor(
                max_archive_uncompressed_bytes=(
                    self.max_archive_uncompressed_bytes
                )
            )
        )

        conversion = config.get(
            "conversion"
        )

        if not isinstance(
            conversion,
            dict,
        ):
            raise ValueError(
                "document.conversion "
                "must be an object"
            )

        if (
            conversion.get("backend")
            != "libreoffice"
        ):
            raise ValueError(
                "document.conversion.backend "
                "must be 'libreoffice'"
            )

        self.conversion_input_extensions = (
            self._extension_set(
                conversion,
                "input_extensions",
                "document.conversion",
            )
        )

        self.conversion_output_formats = (
            self._string_set(
                conversion,
                "output_formats",
                "document.conversion",
            )
        )

        self.max_conversion_output_bytes = (
            self._positive_integer(
                conversion,
                "max_output_bytes",
                "document.conversion",
            )
        )

        if libreoffice_backend is None:
            self.libreoffice_backend = (
                LibreOfficeBackend(
                    executable=conversion.get(
                        "executable"
                    ),
                    timeout_seconds=conversion.get(
                        "timeout_seconds"
                    ),
                )
            )
        else:
            self.libreoffice_backend = (
                libreoffice_backend
            )

        uno = config.get("uno")

        if not isinstance(uno, dict):
            raise ValueError(
                "document.uno must be an object"
            )

        self.uno_input_extensions = (
            self._extension_set(
                uno,
                "input_extensions",
                "document.uno",
            )
        )

        self.uno_output_extensions = (
            self._extension_set(
                uno,
                "output_extensions",
                "document.uno",
            )
        )

        self.max_insert_characters = (
            self._positive_integer(
                uno,
                "max_insert_characters",
                "document.uno",
            )
        )

        self.max_uno_output_bytes = (
            self._positive_integer(
                uno,
                "max_output_bytes",
                "document.uno",
            )
        )

        if uno_backend is None:
            self.uno_backend = (
                LibreOfficeUnoBackend(
                    system_python=uno.get(
                        "system_python"
                    ),
                    soffice_executable=uno.get(
                        "soffice_executable"
                    ),
                    timeout_seconds=uno.get(
                        "timeout_seconds"
                    ),
                )
            )
        else:
            self.uno_backend = uno_backend

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
        except DocumentFailure as exc:
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
        if (
            request.name
            == "document.extract_text"
        ):
            return self._extract_text(
                request.arguments
            )

        if (
            request.name
            == "document.convert"
        ):
            return self._convert(
                request.arguments
            )

        if (
            request.name
            == "document.append_paragraph"
        ):
            return self._append_paragraph(
                request.arguments
            )

        return ActionResult(
            ok=False,
            error="unknown_action",
        )

    def _append_paragraph(
        self,
        arguments: dict[str, Any],
    ) -> ActionResult:
        source_value = arguments.get(
            "source_path"
        )
        target_value = arguments.get(
            "target_path"
        )
        text = arguments.get("text")

        if (
            not isinstance(text, str)
            or not text.strip()
        ):
            raise DocumentFailure(
                "invalid_text"
            )

        if len(text) > (
            self.max_insert_characters
        ):
            raise DocumentFailure(
                "text_too_large"
            )

        try:
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
            raise DocumentFailure(
                exc.code
            ) from exc

        if source_path == target_path:
            raise DocumentFailure(
                "invalid_path"
            )

        source_extension = (
            source_path.suffix.lower()
        )
        target_extension = (
            target_path.suffix.lower()
        )

        if source_extension not in (
            self.uno_input_extensions
        ):
            raise DocumentFailure(
                "unsupported_format"
            )

        if target_extension not in (
            self.uno_output_extensions
        ):
            raise DocumentFailure(
                "invalid_target_extension"
            )

        try:
            source_bytes = (
                source_path.read_bytes()
            )
        except OSError as exc:
            raise DocumentFailure(
                "document_open_failed"
            ) from exc

        if len(source_bytes) > (
            self.max_file_bytes
        ):
            raise DocumentFailure(
                "file_too_large"
            )

        source_sha256 = (
            hashlib.sha256(
                source_bytes
            ).hexdigest()
        )

        work_root = (
            self.filesystem.workspace
            / "work"
        )

        try:
            work_root.mkdir(
                parents=True,
                exist_ok=True,
            )
        except OSError as exc:
            raise DocumentFailure(
                "document_edit_failed"
            ) from exc

        try:
            with tempfile.TemporaryDirectory(
                prefix=".uno-edit-",
                dir=str(work_root),
            ) as temporary_directory:
                temporary_root = Path(
                    temporary_directory
                )

                staged_input_root = (
                    temporary_root / "input"
                )
                staged_output_root = (
                    temporary_root / "output"
                )

                staged_input_root.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                staged_output_root.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                staged_source = (
                    staged_input_root
                    / source_path.name
                )
                staged_target = (
                    staged_output_root
                    / target_path.name
                )

                try:
                    shutil.copy2(
                        source_path,
                        staged_source,
                    )
                except OSError as exc:
                    raise DocumentFailure(
                        "document_open_failed"
                    ) from exc

                try:
                    edit: LibreOfficeEdit = (
                        self.uno_backend
                        .append_paragraph(
                            source=(
                                staged_source
                            ),
                            target=(
                                staged_target
                            ),
                            text=text,
                        )
                    )
                except LibreOfficeUnoFailure as exc:
                    raise DocumentFailure(
                        exc.code
                    ) from exc

                generated_path = (
                    edit
                    .generated_path
                    .resolve()
                )

                resolved_output_root = (
                    staged_output_root.resolve()
                )

                if not generated_path.is_relative_to(
                    resolved_output_root
                ):
                    raise DocumentFailure(
                        "edit_output_mismatch"
                    )

                if generated_path != (
                    staged_target.resolve()
                ):
                    raise DocumentFailure(
                        "edit_output_mismatch"
                    )

                if not generated_path.is_file():
                    raise DocumentFailure(
                        "edit_output_missing"
                    )

                try:
                    generated_bytes = (
                        generated_path
                        .read_bytes()
                    )
                except OSError as exc:
                    raise DocumentFailure(
                        "document_save_failed"
                    ) from exc

                if not generated_bytes:
                    raise DocumentFailure(
                        "edit_output_missing"
                    )

                if len(generated_bytes) > (
                    self.max_uno_output_bytes
                ):
                    raise DocumentFailure(
                        "output_too_large"
                    )

                output_sha256 = (
                    hashlib.sha256(
                        generated_bytes
                    ).hexdigest()
                )

                if (
                    edit.size
                    != len(generated_bytes)
                ):
                    raise DocumentFailure(
                        "edit_output_mismatch"
                    )

                if (
                    edit.sha256
                    != output_sha256
                ):
                    raise DocumentFailure(
                        "edit_output_mismatch"
                    )

                existed = (
                    target_path.exists()
                )

                if existed:
                    try:
                        previous_bytes = (
                            target_path
                            .read_bytes()
                        )
                    except OSError as exc:
                        raise DocumentFailure(
                            "document_save_failed"
                        ) from exc
                else:
                    previous_bytes = None

                changed = (
                    previous_bytes
                    != generated_bytes
                )

                if changed:
                    try:
                        target_path.parent.mkdir(
                            parents=True,
                            exist_ok=True,
                        )

                        generated_path.replace(
                            target_path
                        )
                    except OSError as exc:
                        raise DocumentFailure(
                            "document_save_failed"
                        ) from exc

                    self.database.advance_state_version()

        except DocumentFailure:
            raise
        except OSError as exc:
            raise DocumentFailure(
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
                "sha256": output_sha256,
                "created": not existed,
                "backend": edit.backend,
                "backend_version": (
                    edit.backend_version
                ),
                "backend_stdout": (
                    edit.stdout
                ),
                "backend_stderr": (
                    edit.stderr
                ),
                "execution_evidence": dict(
                    edit.execution_evidence
                ),
            },
            state_changed=changed,
        )

    def _convert(
        self,
        arguments: dict[str, Any],
    ) -> ActionResult:
        source_value = arguments.get(
            "source_path"
        )
        target_value = arguments.get(
            "target_path"
        )
        output_format = arguments.get(
            "format"
        )

        if not isinstance(
            output_format,
            str,
        ):
            raise DocumentFailure(
                "invalid_format"
            )

        output_format = (
            output_format
            .strip()
            .lower()
        )

        if not output_format:
            raise DocumentFailure(
                "invalid_format"
            )

        if output_format not in (
            self.conversion_output_formats
        ):
            raise DocumentFailure(
                "unsupported_output_format"
            )

        try:
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
            raise DocumentFailure(
                exc.code
            ) from exc

        source_extension = (
            source_path.suffix.lower()
        )

        if source_extension not in (
            self.conversion_input_extensions
        ):
            raise DocumentFailure(
                "unsupported_format"
            )

        expected_target_suffix = (
            f".{output_format}"
        )

        if (
            target_path.suffix.lower()
            != expected_target_suffix
        ):
            raise DocumentFailure(
                "invalid_target_extension"
            )

        try:
            source_size = (
                source_path.stat().st_size
            )
        except OSError as exc:
            raise DocumentFailure(
                "conversion_failed"
            ) from exc

        if source_size > (
            self.max_file_bytes
        ):
            raise DocumentFailure(
                "file_too_large"
            )

        work_root = (
            self.filesystem.workspace
            / "work"
        )

        try:
            work_root.mkdir(
                parents=True,
                exist_ok=True,
            )
        except OSError as exc:
            raise DocumentFailure(
                "conversion_failed"
            ) from exc

        try:
            with tempfile.TemporaryDirectory(
                prefix=".libreoffice-",
                dir=str(work_root),
            ) as temporary_directory:
                temporary_root = Path(
                    temporary_directory
                )

                conversion_dir = (
                    temporary_root
                    / "converted"
                )

                conversion: (
                    LibreOfficeConversion
                ) = (
                    self.libreoffice_backend
                    .convert(
                        source=source_path,
                        output_dir=(
                            conversion_dir
                        ),
                        output_format=(
                            output_format
                        ),
                    )
                )

                generated_path = (
                    conversion
                    .generated_path
                    .resolve()
                )

                resolved_conversion_dir = (
                    conversion_dir.resolve()
                )

                if not generated_path.is_relative_to(
                    resolved_conversion_dir
                ):
                    raise DocumentFailure(
                        "conversion_failed"
                    )

                if not generated_path.is_file():
                    raise DocumentFailure(
                        "conversion_output_missing"
                    )

                try:
                    generated_bytes = (
                        generated_path
                        .read_bytes()
                    )
                except OSError as exc:
                    raise DocumentFailure(
                        "conversion_failed"
                    ) from exc

                if not generated_bytes:
                    raise DocumentFailure(
                        "conversion_output_missing"
                    )

                if len(generated_bytes) > (
                    self.max_conversion_output_bytes
                ):
                    raise DocumentFailure(
                        "output_too_large"
                    )

                existed = (
                    target_path.exists()
                )

                if existed:
                    try:
                        previous_bytes = (
                            target_path
                            .read_bytes()
                        )
                    except OSError as exc:
                        raise DocumentFailure(
                            "conversion_failed"
                        ) from exc
                else:
                    previous_bytes = None

                changed = (
                    previous_bytes
                    != generated_bytes
                )

                output_sha256 = (
                    hashlib.sha256(
                        generated_bytes
                    ).hexdigest()
                )

                if changed:
                    try:
                        target_path.parent.mkdir(
                            parents=True,
                            exist_ok=True,
                        )

                        generated_path.replace(
                            target_path
                        )
                    except OSError as exc:
                        raise DocumentFailure(
                            "conversion_failed"
                        ) from exc

                    self.database.advance_state_version()

        except LibreOfficeFailure as exc:
            raise DocumentFailure(
                exc.code
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
                "format": output_format,
                "size": len(
                    generated_bytes
                ),
                "sha256": output_sha256,
                "created": not existed,
                "backend": (
                    conversion.backend
                ),
                "backend_version": (
                    conversion.backend_version
                ),
                "backend_stdout": (
                    conversion.stdout
                ),
                "backend_stderr": (
                    conversion.stderr
                ),
                "execution_evidence": dict(
                    conversion.execution_evidence
                ),
            },
            state_changed=changed,
        )

    def _extract_text(
        self,
        arguments: dict[str, Any],
    ) -> ActionResult:
        requested_path = arguments.get(
            "path"
        )

        try:
            relative, path = (
                self.filesystem
                .resolve_readable_file(
                    requested_path
                )
            )
        except FilesystemFailure as exc:
            raise DocumentFailure(
                exc.code
            ) from exc

        try:
            size = path.stat().st_size
        except OSError as exc:
            raise DocumentFailure(
                "extraction_failed"
            ) from exc

        if size > self.max_file_bytes:
            raise DocumentFailure(
                "file_too_large"
            )

        try:
            content_sha256 = (
                hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
            )
        except OSError as exc:
            raise DocumentFailure(
                "extraction_failed"
            ) from exc

        extension = path.suffix.lower()

        if extension not in (
            self.supported_extensions
        ):
            raise DocumentFailure(
                "unsupported_format"
            )

        try:
            extracted = self.text_extractor.extract(path)
        except TextExtractionFailure as exc:
            raise DocumentFailure(exc.code) from exc

        text = extracted.text

        if len(text) > (
            self.max_text_characters
        ):
            raise DocumentFailure(
                "text_too_large"
            )

        return ActionResult(
            ok=True,
            output={
                "path": relative,
                "media_type": (
                    self._MEDIA_TYPES[
                        extension
                    ]
                ),
                "size": size,
                "sha256": (
                    content_sha256
                ),
                "text": text,
                "character_count": (
                    len(text)
                ),
                "page_count": (
                    extracted.page_count
                ),
                "backend": extracted.backend,
                "warnings": list(extracted.warnings),
                "execution_evidence": dict(
                    extracted.execution_evidence
                ),
            },
            state_changed=False,
        )

    @staticmethod
    def _audit_arguments(
        request: ActionRequest,
    ) -> dict[str, Any]:
        arguments = dict(
            request.arguments
        )

        if (
            request.name
            != "document.append_paragraph"
        ):
            return arguments

        text = arguments.pop(
            "text",
            None,
        )

        if isinstance(text, str):
            encoded = text.encode(
                "utf-8"
            )

            arguments[
                "text_character_count"
            ] = len(text)

            arguments[
                "text_sha256"
            ] = hashlib.sha256(
                encoded
            ).hexdigest()
        else:
            arguments[
                "text_type"
            ] = type(text).__name__

        return arguments

    @staticmethod
    def _extension_set(
        config: dict[str, Any],
        key: str,
        prefix: str,
    ) -> frozenset[str]:
        value = config.get(key)

        if (
            not isinstance(value, list)
            or not value
            or not all(
                isinstance(item, str)
                and item.startswith(".")
                and len(item) > 1
                for item in value
            )
        ):
            raise ValueError(
                f"{prefix}.{key} must be "
                "a non-empty extension list"
            )

        return frozenset(
            item.lower()
            for item in value
        )

    @staticmethod
    def _string_set(
        config: dict[str, Any],
        key: str,
        prefix: str,
    ) -> frozenset[str]:
        value = config.get(key)

        if (
            not isinstance(value, list)
            or not value
            or not all(
                isinstance(item, str)
                and bool(item.strip())
                for item in value
            )
        ):
            raise ValueError(
                f"{prefix}.{key} must be "
                "a non-empty string list"
            )

        return frozenset(
            item.strip().lower()
            for item in value
        )

    @staticmethod
    def _positive_integer(
        config: dict[str, Any],
        key: str,
        prefix: str,
    ) -> int:
        value = config.get(key)

        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value <= 0
        ):
            raise ValueError(
                f"{prefix}.{key} must be "
                "a positive integer"
            )

        return value
