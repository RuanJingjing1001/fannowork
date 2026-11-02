from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import signal
import subprocess
from typing import Any

from fanno_work.backends import BackendFailure


class LibreOfficeUnoFailure(BackendFailure):
    def __init__(
        self,
        code: str,
        detail: str = "",
    ) -> None:
        super().__init__(
            code,
            detail,
            backend="libreoffice-writer-uno",
            timed_out=code == "uno_timeout",
            retryable=code == "uno_timeout",
        )


@dataclass(frozen=True)
class LibreOfficeEdit:
    generated_path: Path
    backend: str
    backend_version: str
    size: int
    sha256: str
    stdout: str
    stderr: str
    execution_evidence: dict[str, Any] = field(
        default_factory=dict
    )


class LibreOfficeUnoBackend:
    """Execute Writer operations through a PyUNO worker."""

    def __init__(
        self,
        *,
        system_python: str,
        soffice_executable: str,
        timeout_seconds: int,
        worker_path: Path | None = None,
    ) -> None:
        if not isinstance(
            system_python,
            str,
        ) or not system_python:
            raise ValueError(
                "UNO system_python must be "
                "a non-empty string"
            )

        if not isinstance(
            soffice_executable,
            str,
        ) or not soffice_executable:
            raise ValueError(
                "UNO soffice_executable must be "
                "a non-empty string"
            )

        if (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise ValueError(
                "UNO timeout_seconds must be "
                "a positive integer"
            )

        if worker_path is None:
            worker_path = (
                Path(__file__)
                .with_name("uno_worker.py")
            )

        self.system_python = system_python
        self.soffice_executable = (
            soffice_executable
        )
        self.timeout_seconds = (
            timeout_seconds
        )
        self.worker_path = Path(
            worker_path
        ).resolve()

    def create_document(
        self,
        *,
        target: Path,
        title: str | None = None,
    ) -> LibreOfficeEdit:
        return self._run_operation(
            operation="create",
            target=target,
            arguments={
                "title": title or "",
            },
        )

    def insert_heading(
        self,
        *,
        source: Path,
        target: Path,
        text: str,
        level: int,
    ) -> LibreOfficeEdit:
        return self._run_operation(
            operation="insert_heading",
            source=source,
            target=target,
            arguments={
                "text": text,
                "level": level,
            },
        )

    def insert_paragraph(
        self,
        *,
        source: Path,
        target: Path,
        text: str,
        style: str | None = None,
    ) -> LibreOfficeEdit:
        return self._run_operation(
            operation="insert_paragraph",
            source=source,
            target=target,
            arguments={
                "text": text,
                "style": style,
            },
        )

    def append_paragraph(
        self,
        *,
        source: Path,
        target: Path,
        text: str,
    ) -> LibreOfficeEdit:
        return self._run_operation(
            operation="append_paragraph",
            source=source,
            target=target,
            arguments={
                "text": text,
            },
        )

    def add_table(
        self,
        *,
        source: Path,
        target: Path,
        headers: list[str],
        rows: list[list[str]],
    ) -> LibreOfficeEdit:
        return self._run_operation(
            operation="add_table",
            source=source,
            target=target,
            arguments={
                "headers": headers,
                "rows": rows,
            },
        )

    def save_as(
        self,
        *,
        source: Path,
        target: Path,
    ) -> LibreOfficeEdit:
        return self._run_operation(
            operation="save_as",
            source=source,
            target=target,
            arguments={},
        )

    def _run_operation(
        self,
        *,
        operation: str,
        target: Path,
        arguments: dict[str, Any],
        source: Path | None = None,
    ) -> LibreOfficeEdit:
        target = Path(target).resolve()

        if source is not None:
            source = Path(source).resolve()

            if not source.is_file():
                raise LibreOfficeUnoFailure(
                    "path_not_found",
                    str(source),
                )

            if source == target:
                raise LibreOfficeUnoFailure(
                    "invalid_path",
                    "source and target must differ",
                )

        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        request: dict[str, Any] = {
            "operation": operation,
            "target_path": str(target),
            "soffice_executable": (
                self.soffice_executable
            ),
            "timeout_seconds": (
                self.timeout_seconds
            ),
            **arguments,
        }

        if source is not None:
            request["source_path"] = str(
                source
            )

        stdout, stderr, return_code = (
            self._execute_worker(request)
        )

        payload = self._parse_response(
            stdout
        )

        if (
            return_code != 0
            or payload.get("ok") is not True
        ):
            raise LibreOfficeUnoFailure(
                str(
                    payload.get(
                        "error",
                        "uno_worker_failed",
                    )
                ),
                str(
                    payload.get(
                        "detail",
                        stderr,
                    )
                ),
            )

        returned_operation = payload.get(
            "operation"
        )

        if returned_operation != operation:
            raise LibreOfficeUnoFailure(
                "invalid_worker_response",
                (
                    f"expected operation "
                    f"{operation!r}, got "
                    f"{returned_operation!r}"
                ),
            )

        returned_target = Path(
            str(
                payload.get(
                    "target_path",
                    "",
                )
            )
        ).resolve()

        if returned_target != target:
            raise LibreOfficeUnoFailure(
                "edit_output_mismatch",
                str(returned_target),
            )

        if not target.is_file():
            raise LibreOfficeUnoFailure(
                "edit_output_missing",
                str(target),
            )

        size = payload.get("size")
        sha256 = payload.get("sha256")
        backend = payload.get("backend")
        backend_version = payload.get(
            "backend_version"
        )
        execution_evidence = payload.get(
            "execution_evidence",
            {},
        )

        if (
            not isinstance(size, int)
            or size <= 0
            or not isinstance(sha256, str)
            or len(sha256) != 64
            or not isinstance(backend, str)
            or not isinstance(
                backend_version,
                str,
            )
            or not isinstance(
                execution_evidence,
                dict,
            )
        ):
            raise LibreOfficeUnoFailure(
                "invalid_worker_response"
            )

        return LibreOfficeEdit(
            generated_path=target,
            backend=backend,
            backend_version=(
                backend_version
            ),
            size=size,
            sha256=sha256,
            stdout=stdout[-4000:],
            stderr=stderr[-4000:],
            execution_evidence=dict(
                execution_evidence
            ),
        )

    def _execute_worker(
        self,
        request: dict[str, Any],
    ) -> tuple[str, str, int]:
        if not self.worker_path.is_file():
            raise LibreOfficeUnoFailure(
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
            raise LibreOfficeUnoFailure(
                "uno_backend_unavailable",
                str(exc),
            ) from exc
        except OSError as exc:
            raise LibreOfficeUnoFailure(
                "uno_worker_failed",
                str(exc),
            ) from exc

        encoded_request = json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
        )

        try:
            stdout, stderr = (
                process.communicate(
                    input=encoded_request,
                    timeout=(
                        self.timeout_seconds
                        + 15
                    ),
                )
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

            raise LibreOfficeUnoFailure(
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
            raise LibreOfficeUnoFailure(
                "invalid_worker_response",
                stdout[-2000:],
            ) from exc

        if not isinstance(payload, dict):
            raise LibreOfficeUnoFailure(
                "invalid_worker_response"
            )

        return payload
