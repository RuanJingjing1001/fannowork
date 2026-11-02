from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import signal
import subprocess
from typing import Any

from fanno_work.backends import BackendFailure, execution_evidence


class LibreOfficeCalcFailure(BackendFailure):
    def __init__(
        self,
        code: str,
        detail: str = "",
    ) -> None:
        super().__init__(
            code,
            detail,
            backend="libreoffice-calc-uno",
            timed_out=code in {
                "uno_timeout",
                "spreadsheet_timeout",
            },
            retryable=code in {
                "uno_timeout",
                "spreadsheet_timeout",
            },
        )


@dataclass(frozen=True)
class LibreOfficeCalcResult:
    operation: str
    backend: str
    backend_version: str
    data: dict[str, Any]
    stdout: str
    stderr: str
    execution_evidence: dict[str, Any] = field(
        default_factory=dict
    )


class LibreOfficeCalcBackend:
    """Execute spreadsheet reads through LibreOffice Calc and PyUNO."""

    def __init__(
        self,
        *,
        system_python: str,
        soffice_executable: str,
        timeout_seconds: int,
        worker_path: Path | None = None,
    ) -> None:
        if not isinstance(system_python, str) or not system_python:
            raise ValueError(
                "spreadsheet UNO system_python "
                "must be a non-empty string"
            )

        if (
            not isinstance(soffice_executable, str)
            or not soffice_executable
        ):
            raise ValueError(
                "spreadsheet UNO soffice_executable "
                "must be a non-empty string"
            )

        if (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise ValueError(
                "spreadsheet UNO timeout_seconds "
                "must be a positive integer"
            )

        if worker_path is None:
            worker_path = Path(__file__).with_name(
                "calc_worker.py"
            )

        self.system_python = system_python
        self.soffice_executable = soffice_executable
        self.timeout_seconds = timeout_seconds
        self.worker_path = Path(worker_path).resolve()

    def inspect_workbook(
        self,
        *,
        source: Path,
    ) -> LibreOfficeCalcResult:
        return self._call(
            {
                "operation": "inspect_workbook",
                "source_path": str(
                    Path(source).resolve()
                ),
            }
        )

    def read_range(
        self,
        *,
        source: Path,
        sheet: str,
        cell_range: str,
        max_range_cells: int,
    ) -> LibreOfficeCalcResult:
        return self._call(
            {
                "operation": "read_range",
                "source_path": str(
                    Path(source).resolve()
                ),
                "sheet": sheet,
                "range": cell_range,
                "max_range_cells": max_range_cells,
            }
        )

    def find(
        self,
        *,
        source: Path,
        query: str,
        sheet: str | None,
        case_sensitive: bool,
        max_results: int,
        max_scan_cells: int,
    ) -> LibreOfficeCalcResult:
        return self._call(
            {
                "operation": "find",
                "source_path": str(
                    Path(source).resolve()
                ),
                "query": query,
                "sheet": sheet,
                "case_sensitive": case_sensitive,
                "max_results": max_results,
                "max_scan_cells": max_scan_cells,
            }
        )

    def _call(
        self,
        request: dict[str, Any],
    ) -> LibreOfficeCalcResult:
        source = Path(
            str(request["source_path"])
        ).resolve()

        if not source.is_file():
            raise LibreOfficeCalcFailure(
                "path_not_found",
                str(source),
            )

        request.update(
            {
                "soffice_executable": (
                    self.soffice_executable
                ),
                "timeout_seconds": (
                    self.timeout_seconds
                ),
            }
        )

        stdout, stderr, return_code = (
            self._execute_worker(request)
        )

        payload = self._parse_response(stdout)

        if (
            return_code != 0
            or payload.get("ok") is not True
        ):
            raise LibreOfficeCalcFailure(
                str(
                    payload.get(
                        "error",
                        "spreadsheet_worker_failed",
                    )
                ),
                str(
                    payload.get(
                        "detail",
                        stderr,
                    )
                ),
            )

        operation = payload.get("operation")
        backend = payload.get("backend")
        backend_version = payload.get(
            "backend_version"
        )
        data = payload.get("data")

        if (
            not isinstance(operation, str)
            or operation != request["operation"]
            or not isinstance(backend, str)
            or not isinstance(
                backend_version,
                str,
            )
            or not isinstance(data, dict)
        ):
            raise LibreOfficeCalcFailure(
                "invalid_worker_response"
            )

        return LibreOfficeCalcResult(
            operation=operation,
            backend=backend,
            backend_version=backend_version,
            data=data,
            stdout=stdout[-4000:],
            stderr=stderr[-4000:],
            execution_evidence=(
                payload.get("execution_evidence")
                if isinstance(
                    payload.get("execution_evidence"),
                    dict,
                )
                else execution_evidence(
                    backend=backend,
                    execution_scope="controller_process",
                    timeout_seconds=self.timeout_seconds,
                    runtime="subprocess",
                )
            ),
        )

    def _execute_worker(
        self,
        request: dict[str, Any],
    ) -> tuple[str, str, int]:
        if not self.worker_path.is_file():
            raise LibreOfficeCalcFailure(
                "uno_backend_unavailable",
                (
                    "worker does not exist: "
                    f"{self.worker_path}"
                ),
            )

        command = [
            self.system_python,
            str(self.worker_path),
        ]

        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise LibreOfficeCalcFailure(
                "uno_backend_unavailable",
                str(exc),
            ) from exc
        except OSError as exc:
            raise LibreOfficeCalcFailure(
                "spreadsheet_worker_failed",
                str(exc),
            ) from exc

        encoded_request = json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
        )

        try:
            stdout, stderr = process.communicate(
                input=encoded_request,
                timeout=self.timeout_seconds + 15,
            )
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(
                    process.pid,
                    signal.SIGKILL,
                )
            except ProcessLookupError:
                pass

            process.communicate()

            raise LibreOfficeCalcFailure(
                "uno_timeout"
            ) from exc

        return (
            stdout,
            stderr,
            int(process.returncode),
        )

    @staticmethod
    def _parse_response(
        stdout: str,
    ) -> dict[str, Any]:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise LibreOfficeCalcFailure(
                "invalid_worker_response",
                stdout[-2000:],
            ) from exc

        if not isinstance(payload, dict):
            raise LibreOfficeCalcFailure(
                "invalid_worker_response"
            )

        return payload
