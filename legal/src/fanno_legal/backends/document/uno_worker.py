from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any
import uuid


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
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WorkerFailure(
            "invalid_request",
            "request is not valid JSON",
        ) from exc

    if not isinstance(value, dict):
        raise WorkerFailure(
            "invalid_request",
            "request must be an object",
        )

    return value


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

    if value is None or value == "":
        return None

    if not isinstance(value, str):
        raise WorkerFailure(
            "invalid_request",
            f"{key} must be a string or null",
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


def require_string_list(
    payload: dict[str, Any],
    key: str,
) -> list[str]:
    value = payload.get(key)

    if (
        not isinstance(value, list)
        or not value
        or not all(
            isinstance(item, str)
            for item in value
        )
    ):
        raise WorkerFailure(
            "invalid_request",
            f"{key} must be a non-empty string list",
        )

    return list(value)


def require_table_rows(
    payload: dict[str, Any],
    key: str,
    columns: int,
) -> list[list[str]]:
    value = payload.get(key)

    if not isinstance(value, list):
        raise WorkerFailure(
            "invalid_request",
            f"{key} must be a list",
        )

    result: list[list[str]] = []

    for row in value:
        if (
            not isinstance(row, list)
            or len(row) != columns
            or not all(
                isinstance(cell, str)
                for cell in row
            )
        ):
            raise WorkerFailure(
                "invalid_request",
                (
                    f"each {key} row must contain "
                    f"{columns} strings"
                ),
            )

        result.append(list(row))

    return result


def resolve_absolute_path(
    raw: str,
    *,
    must_exist: bool,
) -> Path:
    path = Path(raw)

    if not path.is_absolute():
        raise WorkerFailure(
            "invalid_request",
            "worker paths must be absolute",
        )

    path = path.resolve()

    if must_exist and not path.is_file():
        raise WorkerFailure(
            "path_not_found",
            str(path),
        )

    return path


def read_version(
    executable: str,
) -> str:
    try:
        completed = subprocess.run(
            [
                executable,
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
            "com.sun.star.bridge."
            "UnoUrlResolver",
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


def filter_name_for_target(
    target: Path,
) -> str:
    suffix = target.suffix.lower()

    if suffix == ".docx":
        return "Office Open XML Text"

    if suffix == ".odt":
        return "writer8"

    raise WorkerFailure(
        "unsupported_format",
        (
            "Writer worker supports only "
            ".docx and .odt output"
        ),
    )


def create_desktop(
    remote_context: Any,
) -> Any:
    return (
        remote_context
        .ServiceManager
        .createInstanceWithContext(
            "com.sun.star.frame.Desktop",
            remote_context,
        )
    )


def create_writer_document(
    *,
    desktop: Any,
    uno_module: Any,
) -> Any:
    properties = (
        make_property(
            uno_module,
            "Hidden",
            True,
        ),
    )

    try:
        document = (
            desktop.loadComponentFromURL(
                "private:factory/swriter",
                "_blank",
                0,
                properties,
            )
        )
    except Exception as exc:
        raise WorkerFailure(
            "document_create_failed",
            str(exc),
        ) from exc

    if document is None:
        raise WorkerFailure(
            "document_create_failed",
            "LibreOffice returned no document",
        )

    return document


def open_writer_document(
    *,
    desktop: Any,
    uno_module: Any,
    source: Path,
) -> Any:
    properties = (
        make_property(
            uno_module,
            "Hidden",
            True,
        ),
        make_property(
            uno_module,
            "ReadOnly",
            False,
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
            "document_open_failed",
            str(exc),
        ) from exc

    if document is None:
        raise WorkerFailure(
            "document_open_failed",
            "LibreOffice returned no document",
        )

    try:
        is_writer = (
            document.supportsService(
                "com.sun.star.text.TextDocument"
            )
        )
    except Exception as exc:
        close_document(document)

        raise WorkerFailure(
            "document_open_failed",
            str(exc),
        ) from exc

    if not is_writer:
        close_document(document)

        raise WorkerFailure(
            "unsupported_format",
            "file did not open as a Writer document",
        )

    return document


def close_document(
    document: Any,
) -> None:
    try:
        document.close(True)
    except Exception:
        try:
            document.dispose()
        except Exception:
            pass


def insert_paragraph_break_if_needed(
    writer_text: Any,
    cursor: Any,
) -> None:
    cursor.gotoEnd(False)

    if writer_text.getString():
        writer_text.insertControlCharacter(
            cursor,
            0,
            False,
        )


def normalize_paragraph_style(
    value: str | None,
) -> str:
    if value is None:
        return "Text Body"

    aliases = {
        "body text": "Text Body",
        "normal": "Standard",
        "standard": "Standard",
        "text body": "Text Body",
    }

    return aliases.get(
        value.casefold(),
        value,
    )


def apply_insert_heading(
    document: Any,
    payload: dict[str, Any],
) -> None:
    text = require_string(
        payload,
        "text",
    )
    level = require_positive_integer(
        payload,
        "level",
    )

    if level > 10:
        raise WorkerFailure(
            "invalid_heading_level",
            str(level),
        )

    try:
        writer_text = document.Text
        cursor = (
            writer_text.createTextCursor()
        )

        insert_paragraph_break_if_needed(
            writer_text,
            cursor,
        )

        cursor.ParaStyleName = (
            f"Heading {level}"
        )

        writer_text.insertString(
            cursor,
            text,
            False,
        )
    except Exception as exc:
        raise WorkerFailure(
            "document_edit_failed",
            str(exc),
        ) from exc


def apply_insert_paragraph(
    document: Any,
    payload: dict[str, Any],
) -> None:
    text = require_string(
        payload,
        "text",
    )
    style = normalize_paragraph_style(
        optional_string(
            payload,
            "style",
        )
    )

    try:
        writer_text = document.Text
        cursor = (
            writer_text.createTextCursor()
        )

        insert_paragraph_break_if_needed(
            writer_text,
            cursor,
        )

        try:
            cursor.ParaStyleName = style
        except Exception:
            cursor.ParaStyleName = "Standard"

        writer_text.insertString(
            cursor,
            text,
            False,
        )
    except Exception as exc:
        raise WorkerFailure(
            "document_edit_failed",
            str(exc),
        ) from exc


def column_name(
    index: int,
) -> str:
    value = index + 1
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


def apply_add_table(
    document: Any,
    payload: dict[str, Any],
) -> None:
    headers = require_string_list(
        payload,
        "headers",
    )
    rows = require_table_rows(
        payload,
        "rows",
        len(headers),
    )

    row_count = len(rows) + 1
    column_count = len(headers)

    try:
        writer_text = document.Text
        cursor = (
            writer_text.createTextCursor()
        )

        insert_paragraph_break_if_needed(
            writer_text,
            cursor,
        )

        table = document.createInstance(
            "com.sun.star.text.TextTable"
        )

        table.initialize(
            row_count,
            column_count,
        )

        writer_text.insertTextContent(
            cursor,
            table,
            False,
        )

        for column, header in enumerate(
            headers
        ):
            cell = table.getCellByName(
                f"{column_name(column)}1"
            )
            cell.setString(header)

            cell_cursor = (
                cell.createTextCursor()
            )
            cell_cursor.gotoEnd(True)
            cell_cursor.CharWeight = 150.0

        for row_index, row in enumerate(
            rows,
            start=2,
        ):
            for column, value in enumerate(
                row
            ):
                cell = table.getCellByName(
                    (
                        f"{column_name(column)}"
                        f"{row_index}"
                    )
                )
                cell.setString(value)

    except Exception as exc:
        raise WorkerFailure(
            "document_edit_failed",
            str(exc),
        ) from exc


def save_document(
    *,
    document: Any,
    uno_module: Any,
    target: Path,
) -> None:
    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    properties = (
        make_property(
            uno_module,
            "FilterName",
            filter_name_for_target(
                target
            ),
        ),
        make_property(
            uno_module,
            "Overwrite",
            True,
        ),
    )

    try:
        document.storeAsURL(
            target.as_uri(),
            properties,
        )
    except Exception as exc:
        raise WorkerFailure(
            "document_save_failed",
            str(exc),
        ) from exc

    if not target.is_file():
        raise WorkerFailure(
            "edit_output_missing",
            str(target),
        )


def run_operation(
    *,
    payload: dict[str, Any],
    document: Any,
) -> None:
    operation = require_string(
        payload,
        "operation",
    )

    if operation == "create":
        title = optional_string(
            payload,
            "title",
        )

        if title:
            try:
                (
                    document
                    .DocumentProperties
                    .Title
                ) = title
            except Exception as exc:
                raise WorkerFailure(
                    "document_edit_failed",
                    str(exc),
                ) from exc

        return

    if operation == "insert_heading":
        apply_insert_heading(
            document,
            payload,
        )
        return

    if operation in {
        "insert_paragraph",
        "append_paragraph",
    }:
        apply_insert_paragraph(
            document,
            payload,
        )
        return

    if operation == "add_table":
        apply_add_table(
            document,
            payload,
        )
        return

    if operation == "save_as":
        return

    raise WorkerFailure(
        "unknown_operation",
        operation,
    )


def run_request(
    payload: dict[str, Any],
) -> dict[str, Any]:
    started = time.monotonic()

    operation = require_string(
        payload,
        "operation",
    )

    target = resolve_absolute_path(
        require_string(
            payload,
            "target_path",
        ),
        must_exist=False,
    )

    source: Path | None = None

    if operation != "create":
        source = resolve_absolute_path(
            require_string(
                payload,
                "source_path",
            ),
            must_exist=True,
        )

        if source == target:
            raise WorkerFailure(
                "invalid_request",
                "source and target must differ",
            )

        if source.suffix.lower() not in {
            ".docx",
            ".odt",
        }:
            raise WorkerFailure(
                "unsupported_format",
                str(source),
            )

    if target.suffix.lower() not in {
        ".docx",
        ".odt",
    }:
        raise WorkerFailure(
            "unsupported_format",
            str(target),
        )

    executable = require_string(
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
        "fanno_writer_"
        + uuid.uuid4().hex
    )

    version = read_version(
        executable
    )

    connect_ms: float | None = None
    open_ms: float | None = None
    operation_ms: float | None = None

    with tempfile.TemporaryDirectory(
        prefix="fanno-writer-profile-"
    ) as profile_directory:
        profile_path = Path(
            profile_directory
        ).resolve()

        command = [
            executable,
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
            phase_started = (
                time.monotonic()
            )

            remote_context = (
                connect_to_office(
                    uno_module=uno,
                    pipe_name=pipe_name,
                    process=process,
                    timeout_seconds=(
                        timeout_seconds
                    ),
                )
            )

            connect_ms = (
                time.monotonic()
                - phase_started
            ) * 1000

            desktop = create_desktop(
                remote_context
            )

            phase_started = (
                time.monotonic()
            )

            if operation == "create":
                document = (
                    create_writer_document(
                        desktop=desktop,
                        uno_module=uno,
                    )
                )
            else:
                assert source is not None

                document = (
                    open_writer_document(
                        desktop=desktop,
                        uno_module=uno,
                        source=source,
                    )
                )

            open_ms = (
                time.monotonic()
                - phase_started
            ) * 1000

            phase_started = (
                time.monotonic()
            )

            run_operation(
                payload=payload,
                document=document,
            )

            save_document(
                document=document,
                uno_module=uno,
                target=target,
            )

            operation_ms = (
                time.monotonic()
                - phase_started
            ) * 1000

        finally:
            if document is not None:
                close_document(document)

            terminate_process(process)

    output_bytes = target.read_bytes()

    return {
        "ok": True,
        "operation": operation,
        "backend": "libreoffice-writer-uno",
        "backend_version": version,
        "target_path": str(target),
        "size": len(output_bytes),
        "sha256": hashlib.sha256(
            output_bytes
        ).hexdigest(),
        "execution_evidence": {
            "transport": "uno_pipe",
            "opened_read_only": False,
            "source_present": (
                source is not None
            ),
            "target_format": (
                target.suffix.lower()
            ),
            "connect_ms": (
                round(connect_ms, 3)
                if connect_ms is not None
                else None
            ),
            "open_ms": (
                round(open_ms, 3)
                if open_ms is not None
                else None
            ),
            "operation_ms": (
                round(operation_ms, 3)
                if operation_ms is not None
                else None
            ),
            "total_ms": round(
                (
                    time.monotonic()
                    - started
                )
                * 1000,
                3,
            ),
        },
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
            "error": "uno_worker_failed",
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