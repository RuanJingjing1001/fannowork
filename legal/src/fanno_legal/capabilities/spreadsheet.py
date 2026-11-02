from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fanno_work.models import (
    ActionRequest,
    ActionResult,
)

from fanno_legal.backends.spreadsheet.libreoffice_calc import (
    LibreOfficeCalcBackend,
    LibreOfficeCalcFailure,
    LibreOfficeCalcResult,
)
from fanno_legal.capabilities.filesystem import (
    FilesystemCapability,
    FilesystemFailure,
)
from fanno_legal.state.audit import AuditLog
from fanno_legal.state.database import StateDatabase


class SpreadsheetFailure(Exception):
    def __init__(
        self,
        code: str,
    ) -> None:
        super().__init__(code)
        self.code = code


class SpreadsheetCapability:
    ACTION_NAMES = frozenset(
        {
            "spreadsheet.inspect_workbook",
            "spreadsheet.read_range",
            "spreadsheet.find",
        }
    )

    def __init__(
        self,
        filesystem: FilesystemCapability,
        database: StateDatabase,
        audit: AuditLog,
        config: dict[str, Any],
        backend: (
            LibreOfficeCalcBackend | None
        ) = None,
    ) -> None:
        self.filesystem = filesystem
        self.database = database
        self.audit = audit

        self.supported_extensions = (
            self._extension_set(
                config.get(
                    "supported_extensions"
                )
            )
        )
        self.max_file_bytes = (
            self._positive_integer(
                config,
                "max_file_bytes",
            )
        )
        self.max_range_cells = (
            self._positive_integer(
                config,
                "max_range_cells",
            )
        )
        self.max_scan_cells = (
            self._positive_integer(
                config,
                "max_scan_cells",
            )
        )
        self.max_query_characters = (
            self._positive_integer(
                config,
                "max_query_characters",
            )
        )
        self.max_results = (
            self._positive_integer(
                config,
                "max_results",
            )
        )

        uno = config.get("uno")

        if not isinstance(uno, dict):
            raise ValueError(
                "spreadsheet.uno must be an object"
            )

        if backend is None:
            self.backend = (
                LibreOfficeCalcBackend(
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
        except SpreadsheetFailure as exc:
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
            arguments=dict(
                request.arguments
            ),
            ok=result.ok,
            error=result.error,
            state_version_before=version_before,
            state_version_after=version_after,
        )

        return result

    def _dispatch(
        self,
        request: ActionRequest,
    ) -> ActionResult:
        if (
            request.name
            == "spreadsheet.inspect_workbook"
        ):
            return self._inspect_workbook(
                request.arguments
            )

        if (
            request.name
            == "spreadsheet.read_range"
        ):
            return self._read_range(
                request.arguments
            )

        if (
            request.name
            == "spreadsheet.find"
        ):
            return self._find(
                request.arguments
            )

        return ActionResult(
            ok=False,
            error="unknown_action",
        )

    def _inspect_workbook(
        self,
        arguments: dict[str, Any],
    ) -> ActionResult:
        relative, source, source_hash = (
            self._resolve_source(
                arguments.get("path")
            )
        )

        try:
            result = (
                self.backend
                .inspect_workbook(
                    source=source
                )
            )
        except LibreOfficeCalcFailure as exc:
            raise SpreadsheetFailure(
                exc.code
            ) from exc

        return self._success(
            relative=relative,
            source_hash=source_hash,
            result=result,
        )

    def _read_range(
        self,
        arguments: dict[str, Any],
    ) -> ActionResult:
        sheet = arguments.get("sheet")
        cell_range = arguments.get(
            "range"
        )

        if not isinstance(sheet, str) or not sheet:
            raise SpreadsheetFailure(
                "invalid_sheet"
            )

        if (
            not isinstance(cell_range, str)
            or not cell_range
        ):
            raise SpreadsheetFailure(
                "invalid_range"
            )

        relative, source, source_hash = (
            self._resolve_source(
                arguments.get("path")
            )
        )

        try:
            result = self.backend.read_range(
                source=source,
                sheet=sheet,
                cell_range=cell_range,
                max_range_cells=(
                    self.max_range_cells
                ),
            )
        except LibreOfficeCalcFailure as exc:
            raise SpreadsheetFailure(
                exc.code
            ) from exc

        return self._success(
            relative=relative,
            source_hash=source_hash,
            result=result,
        )

    def _find(
        self,
        arguments: dict[str, Any],
    ) -> ActionResult:
        query = arguments.get("query")
        sheet = arguments.get("sheet")
        case_sensitive = arguments.get(
            "case_sensitive",
            False,
        )
        requested_max_results = (
            arguments.get(
                "max_results",
                self.max_results,
            )
        )

        if (
            not isinstance(query, str)
            or not query
        ):
            raise SpreadsheetFailure(
                "invalid_query"
            )

        if (
            len(query)
            > self.max_query_characters
        ):
            raise SpreadsheetFailure(
                "query_too_large"
            )

        if (
            sheet is not None
            and (
                not isinstance(sheet, str)
                or not sheet
            )
        ):
            raise SpreadsheetFailure(
                "invalid_sheet"
            )

        if not isinstance(
            case_sensitive,
            bool,
        ):
            raise SpreadsheetFailure(
                "invalid_query"
            )

        if (
            not isinstance(
                requested_max_results,
                int,
            )
            or isinstance(
                requested_max_results,
                bool,
            )
            or requested_max_results <= 0
            or requested_max_results
            > self.max_results
        ):
            raise SpreadsheetFailure(
                "invalid_result_limit"
            )

        relative, source, source_hash = (
            self._resolve_source(
                arguments.get("path")
            )
        )

        try:
            result = self.backend.find(
                source=source,
                query=query,
                sheet=sheet,
                case_sensitive=(
                    case_sensitive
                ),
                max_results=(
                    requested_max_results
                ),
                max_scan_cells=(
                    self.max_scan_cells
                ),
            )
        except LibreOfficeCalcFailure as exc:
            raise SpreadsheetFailure(
                exc.code
            ) from exc

        return self._success(
            relative=relative,
            source_hash=source_hash,
            result=result,
        )

    def _resolve_source(
        self,
        value: Any,
    ) -> tuple[str, Path, str]:
        try:
            relative, source = (
                self.filesystem
                .resolve_readable_file(
                    value
                )
            )
        except FilesystemFailure as exc:
            raise SpreadsheetFailure(
                exc.code
            ) from exc

        if (
            source.suffix.lower()
            not in self.supported_extensions
        ):
            raise SpreadsheetFailure(
                "unsupported_format"
            )

        try:
            content = source.read_bytes()
        except OSError as exc:
            raise SpreadsheetFailure(
                "spreadsheet_open_failed"
            ) from exc

        if len(content) > self.max_file_bytes:
            raise SpreadsheetFailure(
                "file_too_large"
            )

        return (
            relative,
            source,
            hashlib.sha256(
                content
            ).hexdigest(),
        )

    @staticmethod
    def _success(
        *,
        relative: str,
        source_hash: str,
        result: LibreOfficeCalcResult,
    ) -> ActionResult:
        return ActionResult(
            ok=True,
            output={
                "path": relative,
                "source_sha256": source_hash,
                "backend": result.backend,
                "backend_version": (
                    result.backend_version
                ),
                "execution_evidence": dict(
                    result.execution_evidence
                ),
                **result.data,
            },
            state_changed=False,
        )

    @staticmethod
    def _positive_integer(
        config: dict[str, Any],
        key: str,
    ) -> int:
        value = config.get(key)

        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value <= 0
        ):
            raise ValueError(
                f"spreadsheet.{key} must "
                "be a positive integer"
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
                and item == item.lower()
                for item in value
            )
        ):
            raise ValueError(
                "spreadsheet.supported_extensions "
                "must be a non-empty list of "
                "lowercase extensions"
            )

        return frozenset(value)
