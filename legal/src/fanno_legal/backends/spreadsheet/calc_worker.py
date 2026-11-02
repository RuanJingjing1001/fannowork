from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any
import uuid


A1_RANGE = re.compile(
    r"^[A-Za-z]{1,3}[1-9][0-9]*"
    r"(?::[A-Za-z]{1,3}[1-9][0-9]*)?$"
)


class WorkerFailure(Exception):
    def __init__(
        self,
        code: str,
        detail: str = "",
    ) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def make_property(
    uno_module: Any,
    name: str,
    value: Any,
) -> Any:
    prop = uno_module.createUnoStruct(
        "com.sun.star.beans.PropertyValue"
    )
    prop.Name = name
    prop.Value = value
    return prop


def read_request() -> dict[str, Any]:
    raw = sys.stdin.read()

    if not raw.strip():
        raise WorkerFailure(
            "invalid_request",
            "request body is empty",
        )

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WorkerFailure(
            "invalid_request",
            "request is not valid JSON",
        ) from exc

    if not isinstance(payload, dict):
        raise WorkerFailure(
            "invalid_request",
            "request must be an object",
        )

    return payload


def require_string(
    payload: dict[str, Any],
    key: str,
) -> str:
    value = payload.get(key)

    if not isinstance(value, str) or not value:
        raise WorkerFailure(
            "invalid_request",
            f"{key} must be a non-empty string",
        )

    return value


def optional_string(
    payload: dict[str, Any],
    key: str,
) -> str | None:
    value = payload.get(key)

    if value is None:
        return None

    if not isinstance(value, str) or not value:
        raise WorkerFailure(
            "invalid_request",
            f"{key} must be null or a non-empty string",
        )

    return value


def require_positive_integer(
    payload: dict[str, Any],
    key: str,
) -> int:
    value = payload.get(key)

    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
    ):
        raise WorkerFailure(
            "invalid_request",
            f"{key} must be a positive integer",
        )

    return value


def require_boolean(
    payload: dict[str, Any],
    key: str,
) -> bool:
    value = payload.get(key)

    if not isinstance(value, bool):
        raise WorkerFailure(
            "invalid_request",
            f"{key} must be a boolean",
        )

    return value


def resolve_source(
    raw: str,
) -> Path:
    path = Path(raw)

    if not path.is_absolute():
        raise WorkerFailure(
            "invalid_request",
            "source_path must be absolute",
        )

    path = path.resolve()

    if not path.is_file():
        raise WorkerFailure(
            "path_not_found",
            str(path),
        )

    if path.suffix.lower() not in {
        ".xlsx",
        ".ods",
        ".xls",
    }:
        raise WorkerFailure(
            "unsupported_format",
            str(path),
        )

    return path


def read_version(
    soffice_executable: str,
) -> str:
    try:
        completed = subprocess.run(
            [
                soffice_executable,
                "--version",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            check=False,
        )
    except (
        FileNotFoundError,
        OSError,
        subprocess.TimeoutExpired,
    ):
        return "unknown"

    if completed.returncode != 0:
        return "unknown"

    return (
        completed.stdout.strip()
        or "unknown"
    )


def connect_to_office(
    *,
    uno_module: Any,
    pipe_name: str,
    process: subprocess.Popen,
    timeout_seconds: int,
) -> Any:
    local_context = (
        uno_module.getComponentContext()
    )

    resolver = (
        local_context
        .ServiceManager
        .createInstanceWithContext(
            "com.sun.star.bridge.UnoUrlResolver",
            local_context,
        )
    )

    connection_url = (
        "uno:pipe,"
        f"name={pipe_name};"
        "urp;"
        "StarOffice.ComponentContext"
    )

    deadline = (
        time.monotonic()
        + timeout_seconds
    )

    last_error: Exception | None = None

    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise WorkerFailure(
                "uno_process_exited",
                (
                    "LibreOffice exited before "
                    "UNO connection was ready"
                ),
            )

        try:
            return resolver.resolve(
                connection_url
            )
        except Exception as exc:
            last_error = exc
            time.sleep(0.2)

    raise WorkerFailure(
        "uno_connection_failed",
        (
            str(last_error)
            if last_error is not None
            else "connection timed out"
        ),
    )


def terminate_process(
    process: subprocess.Popen,
) -> None:
    if process.poll() is not None:
        return

    try:
        process.terminate()
        process.wait(timeout=5)
        return
    except (
        subprocess.TimeoutExpired,
        ProcessLookupError,
    ):
        pass

    try:
        os.killpg(
            os.getpgid(process.pid),
            signal.SIGKILL,
        )
    except (
        ProcessLookupError,
        PermissionError,
    ):
        try:
            process.kill()
        except ProcessLookupError:
            pass

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def column_label(
    column: int,
) -> str:
    value = column + 1
    result = ""

    while value:
        value, remainder = divmod(
            value - 1,
            26,
        )
        result = (
            chr(65 + remainder)
            + result
        )

    return result


def cell_address(
    column: int,
    row: int,
) -> str:
    return (
        f"{column_label(column)}"
        f"{row + 1}"
    )


def content_type(
    cell: Any,
) -> str:
    cell_type = cell.getType()
    value = getattr(
        cell_type,
        "value",
        str(cell_type),
    )
    return str(value).lower()


def cell_payload(
    cell: Any,
    *,
    column: int,
    row: int,
) -> dict[str, Any]:
    kind = content_type(cell)
    display_text = cell.getString()

    if kind == "empty":
        value: Any = None
    elif kind == "value":
        value = cell.getValue()
    else:
        value = display_text

    formula: str | None = None

    if kind == "formula":
        formula = cell.getFormula()

    return {
        "address": cell_address(
            column,
            row,
        ),
        "type": kind,
        "value": value,
        "display_text": display_text,
        "formula": formula,
    }


def used_range(
    sheet: Any,
) -> dict[str, Any]:
    try:
        cursor = sheet.createCursor()
        cursor.gotoEndOfUsedArea(True)
        address = cursor.getRangeAddress()
    except Exception as exc:
        raise WorkerFailure(
            "spreadsheet_read_failed",
            str(exc),
        ) from exc

    start_column = int(
        address.StartColumn
    )
    start_row = int(
        address.StartRow
    )
    end_column = int(
        address.EndColumn
    )
    end_row = int(
        address.EndRow
    )

    first_cell = sheet.getCellByPosition(
        start_column,
        start_row,
    )

    if (
        start_column == 0
        and start_row == 0
        and end_column == 0
        and end_row == 0
        and content_type(first_cell) == "empty"
    ):
        return {
            "range": None,
            "rows": 0,
            "columns": 0,
            "cell_count": 0,
        }

    rows = end_row - start_row + 1
    columns = (
        end_column
        - start_column
        + 1
    )

    return {
        "range": (
            f"{cell_address(start_column, start_row)}:"
            f"{cell_address(end_column, end_row)}"
        ),
        "rows": rows,
        "columns": columns,
        "cell_count": rows * columns,
        "start_column": start_column,
        "start_row": start_row,
        "end_column": end_column,
        "end_row": end_row,
    }


def get_sheet(
    document: Any,
    sheet_name: str,
) -> Any:
    sheets = document.getSheets()

    if not sheets.hasByName(
        sheet_name
    ):
        raise WorkerFailure(
            "sheet_not_found",
            sheet_name,
        )

    return sheets.getByName(
        sheet_name
    )


def inspect_workbook(
    document: Any,
) -> dict[str, Any]:
    sheets = document.getSheets()
    names = list(
        sheets.getElementNames()
    )

    result: list[dict[str, Any]] = []

    for index, name in enumerate(names):
        sheet = sheets.getByName(name)
        extent = used_range(sheet)

        result.append(
            {
                "index": index,
                "name": name,
                "used_range": extent["range"],
                "rows": extent["rows"],
                "columns": extent["columns"],
                "cell_count": extent["cell_count"],
            }
        )

    return {
        "sheet_count": len(result),
        "sheets": result,
    }


def read_range(
    document: Any,
    *,
    sheet_name: str,
    range_name: str,
    max_range_cells: int,
) -> dict[str, Any]:
    normalized_range = (
        range_name.strip().upper()
    )

    if not A1_RANGE.fullmatch(
        normalized_range
    ):
        raise WorkerFailure(
            "invalid_range",
            range_name,
        )

    sheet = get_sheet(
        document,
        sheet_name,
    )

    try:
        selected = (
            sheet.getCellRangeByName(
                normalized_range
            )
        )
        address = (
            selected.getRangeAddress()
        )
    except Exception as exc:
        raise WorkerFailure(
            "invalid_range",
            str(exc),
        ) from exc

    start_column = int(
        address.StartColumn
    )
    start_row = int(
        address.StartRow
    )
    end_column = int(
        address.EndColumn
    )
    end_row = int(
        address.EndRow
    )

    rows = end_row - start_row + 1
    columns = (
        end_column
        - start_column
        + 1
    )
    cell_count = rows * columns

    if cell_count > max_range_cells:
        raise WorkerFailure(
            "range_too_large",
            (
                f"requested {cell_count} cells; "
                f"limit is {max_range_cells}"
            ),
        )

    cells: list[list[dict[str, Any]]] = []

    for row in range(
        start_row,
        end_row + 1,
    ):
        row_cells: list[
            dict[str, Any]
        ] = []

        for column in range(
            start_column,
            end_column + 1,
        ):
            cell = sheet.getCellByPosition(
                column,
                row,
            )
            row_cells.append(
                cell_payload(
                    cell,
                    column=column,
                    row=row,
                )
            )

        cells.append(row_cells)

    return {
        "sheet": sheet_name,
        "range": normalized_range,
        "rows": rows,
        "columns": columns,
        "cell_count": cell_count,
        "cells": cells,
    }


def find_cells(
    document: Any,
    *,
    query: str,
    sheet_name: str | None,
    case_sensitive: bool,
    max_results: int,
    max_scan_cells: int,
) -> dict[str, Any]:
    sheets = document.getSheets()

    if sheet_name is None:
        sheet_names = list(
            sheets.getElementNames()
        )
    else:
        get_sheet(
            document,
            sheet_name,
        )
        sheet_names = [sheet_name]

    extents: list[
        tuple[str, Any, dict[str, Any]]
    ] = []
    scan_cell_count = 0

    for name in sheet_names:
        sheet = sheets.getByName(name)
        extent = used_range(sheet)
        scan_cell_count += int(
            extent["cell_count"]
        )
        extents.append(
            (
                name,
                sheet,
                extent,
            )
        )

    if scan_cell_count > max_scan_cells:
        raise WorkerFailure(
            "workbook_too_large",
            (
                f"search would scan "
                f"{scan_cell_count} cells; "
                f"limit is {max_scan_cells}"
            ),
        )

    comparison_query = (
        query
        if case_sensitive
        else query.casefold()
    )

    matches: list[
        dict[str, Any]
    ] = []
    total_matches = 0

    for name, sheet, extent in extents:
        if extent["cell_count"] == 0:
            continue

        for row in range(
            int(extent["start_row"]),
            int(extent["end_row"]) + 1,
        ):
            for column in range(
                int(extent["start_column"]),
                int(extent["end_column"]) + 1,
            ):
                cell = (
                    sheet.getCellByPosition(
                        column,
                        row,
                    )
                )
                display_text = (
                    cell.getString()
                )
                comparison_text = (
                    display_text
                    if case_sensitive
                    else display_text.casefold()
                )

                if (
                    comparison_query
                    not in comparison_text
                ):
                    continue

                total_matches += 1

                if len(matches) < max_results:
                    match = cell_payload(
                        cell,
                        column=column,
                        row=row,
                    )
                    match["sheet"] = name
                    matches.append(match)

    return {
        "query": query,
        "sheet": sheet_name,
        "case_sensitive": case_sensitive,
        "scan_cell_count": scan_cell_count,
        "total_matches": total_matches,
        "returned_matches": len(matches),
        "truncated": (
            total_matches > len(matches)
        ),
        "matches": matches,
    }


def open_workbook(
    *,
    uno_module: Any,
    remote_context: Any,
    source: Path,
) -> Any:
    service_manager = (
        remote_context.ServiceManager
    )

    desktop = (
        service_manager
        .createInstanceWithContext(
            "com.sun.star.frame.Desktop",
            remote_context,
        )
    )

    properties = (
        make_property(
            uno_module,
            "Hidden",
            True,
        ),
        make_property(
            uno_module,
            "ReadOnly",
            True,
        ),
    )

    try:
        document = (
            desktop.loadComponentFromURL(
                source.as_uri(),
                "_blank",
                0,
                properties,
            )
        )
    except Exception as exc:
        raise WorkerFailure(
            "spreadsheet_open_failed",
            str(exc),
        ) from exc

    if document is None:
        raise WorkerFailure(
            "spreadsheet_open_failed",
            "LibreOffice returned no document",
        )

    try:
        is_spreadsheet = (
            document.supportsService(
                "com.sun.star.sheet."
                "SpreadsheetDocument"
            )
        )
    except Exception as exc:
        document.dispose()
        raise WorkerFailure(
            "spreadsheet_open_failed",
            str(exc),
        ) from exc

    if not is_spreadsheet:
        document.dispose()
        raise WorkerFailure(
            "unsupported_format",
            "file did not open as a spreadsheet",
        )

    return document


def close_document(
    document: Any,
) -> None:
    try:
        document.close(True)
    except Exception:
        document.dispose()


def run_operation(
    *,
    payload: dict[str, Any],
    document: Any,
) -> dict[str, Any]:
    operation = require_string(
        payload,
        "operation",
    )

    if operation == "inspect_workbook":
        return inspect_workbook(
            document
        )

    if operation == "read_range":
        return read_range(
            document,
            sheet_name=require_string(
                payload,
                "sheet",
            ),
            range_name=require_string(
                payload,
                "range",
            ),
            max_range_cells=(
                require_positive_integer(
                    payload,
                    "max_range_cells",
                )
            ),
        )

    if operation == "find":
        return find_cells(
            document,
            query=require_string(
                payload,
                "query",
            ),
            sheet_name=optional_string(
                payload,
                "sheet",
            ),
            case_sensitive=require_boolean(
                payload,
                "case_sensitive",
            ),
            max_results=(
                require_positive_integer(
                    payload,
                    "max_results",
                )
            ),
            max_scan_cells=(
                require_positive_integer(
                    payload,
                    "max_scan_cells",
                )
            ),
        )

    raise WorkerFailure(
        "unknown_operation",
        operation,
    )


def run_request(
    payload: dict[str, Any],
) -> dict[str, Any]:
    operation = require_string(
        payload,
        "operation",
    )
    source = resolve_source(
        require_string(
            payload,
            "source_path",
        )
    )
    soffice_executable = require_string(
        payload,
        "soffice_executable",
    )
    timeout_seconds = (
        require_positive_integer(
            payload,
            "timeout_seconds",
        )
    )

    try:
        import uno
    except ImportError as exc:
        raise WorkerFailure(
            "uno_backend_unavailable",
            str(exc),
        ) from exc

    pipe_name = (
        "fanno_calc_"
        + uuid.uuid4().hex
    )
    backend_version = read_version(
        soffice_executable
    )

    with tempfile.TemporaryDirectory(
        prefix="fanno-calc-profile-"
    ) as profile_directory:
        profile_path = Path(
            profile_directory
        ).resolve()

        command = [
            soffice_executable,
            "--headless",
            "--nologo",
            "--nodefault",
            "--norestore",
            "--nolockcheck",
            (
                "-env:UserInstallation="
                f"{profile_path.as_uri()}"
            ),
            (
                "--accept="
                f"pipe,name={pipe_name};"
                "urp;"
                "StarOffice.ComponentContext"
            ),
        ]

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise WorkerFailure(
                "uno_backend_unavailable",
                str(exc),
            ) from exc
        except OSError as exc:
            raise WorkerFailure(
                "uno_start_failed",
                str(exc),
            ) from exc

        document = None

        try:
            remote_context = connect_to_office(
                uno_module=uno,
                pipe_name=pipe_name,
                process=process,
                timeout_seconds=timeout_seconds,
            )
            document = open_workbook(
                uno_module=uno,
                remote_context=remote_context,
                source=source,
            )
            data = run_operation(
                payload=payload,
                document=document,
            )
        finally:
            if document is not None:
                close_document(document)
            terminate_process(process)

    return {
        "ok": True,
        "operation": operation,
        "backend": "libreoffice-calc-uno",
        "backend_version": backend_version,
        "data": data,
    }


def main() -> int:
    try:
        payload = read_request()
        result = run_request(payload)
    except WorkerFailure as exc:
        result = {
            "ok": False,
            "error": exc.code,
            "detail": exc.detail[-2000:],
        }
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    except Exception as exc:
        result = {
            "ok": False,
            "error": "spreadsheet_worker_failed",
            "detail": str(exc)[-2000:],
        }
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())