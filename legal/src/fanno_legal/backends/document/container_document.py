"""Document backends executed inside one task Docker container."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fanno_work.sandbox import ContainerWorkspace, ContainerWorkspaceError

from .libreoffice_cli import (
    LibreOfficeBackend,
    LibreOfficeConversion,
    LibreOfficeFailure,
)
from .text_extractor import (
    ExtractedText,
    TextExtractionFailure,
    TextExtractor,
)


class _ContainerDocumentWorker:
    def __init__(
        self,
        *,
        workspace: ContainerWorkspace,
        timeout_seconds: int,
        python_executable: str = "python",
        worker_path: str = "/opt/fanno/workers/document_worker.py",
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.workspace = workspace
        self.timeout_seconds = timeout_seconds
        self.python_executable = python_executable
        self.worker_path = worker_path

    def _call(
        self,
        request: dict[str, Any],
        *,
        timeout_error: str,
        unavailable_error: str,
    ) -> dict[str, Any]:
        try:
            completed = self.workspace.execute(
                [self.python_executable, self.worker_path],
                input_text=json.dumps(
                    request,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                timeout_seconds=self.timeout_seconds + 15,
            )
        except ContainerWorkspaceError as exc:
            code = (
                timeout_error
                if exc.code == "container_timeout"
                else unavailable_error
            )
            raise LibreOfficeFailure(
                code,
                exc.detail or exc.code,
            ) from exc

        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise LibreOfficeFailure(
                "invalid_backend_response",
                completed.stdout[-2000:],
            ) from exc

        if not isinstance(payload, dict):
            raise LibreOfficeFailure("invalid_backend_response")
        if completed.return_code != 0 or payload.get("ok") is not True:
            backend_error = payload.get("backend_error")
            detail = (
                str(backend_error.get("detail", ""))
                if isinstance(backend_error, dict)
                else completed.stderr[-2000:]
            )
            raise LibreOfficeFailure(
                str(payload.get("error", unavailable_error)),
                detail,
            )
        return payload


class ContainerTextExtractor(TextExtractor, _ContainerDocumentWorker):
    """Extract text through the Python runtime inside the task container."""

    def __init__(
        self,
        *,
        workspace: ContainerWorkspace,
        max_archive_uncompressed_bytes: int,
        timeout_seconds: int,
    ) -> None:
        TextExtractor.__init__(
            self,
            max_archive_uncompressed_bytes=(
                max_archive_uncompressed_bytes
            ),
        )
        _ContainerDocumentWorker.__init__(
            self,
            workspace=workspace,
            timeout_seconds=timeout_seconds,
        )

    def extract(self, path: Path) -> ExtractedText:
        try:
            payload = self._call(
                {
                    "operation": "extract_text",
                    "source_path": self.workspace.to_container_path(path),
                    "max_archive_uncompressed_bytes": (
                        self.max_archive_uncompressed_bytes
                    ),
                    "timeout_seconds": self.timeout_seconds,
                },
                timeout_error="extraction_timeout",
                unavailable_error="backend_unavailable",
            )
        except LibreOfficeFailure as exc:
            raise TextExtractionFailure(
                exc.code,
                exc.detail,
            ) from exc

        text = payload.get("text")
        page_count = payload.get("page_count")
        backend = payload.get("backend")
        warnings = payload.get("warnings", [])
        evidence = payload.get("execution_evidence", {})
        if (
            not isinstance(text, str)
            or not (
                page_count is None
                or isinstance(page_count, int)
            )
            or not isinstance(backend, str)
            or not isinstance(warnings, list)
            or not all(isinstance(item, str) for item in warnings)
            or not isinstance(evidence, dict)
        ):
            raise TextExtractionFailure("invalid_backend_response")

        return ExtractedText(
            text=text,
            page_count=page_count,
            backend=backend,
            warnings=tuple(warnings),
            execution_evidence=evidence,
        )


class ContainerLibreOfficeConversionBackend(
    LibreOfficeBackend,
    _ContainerDocumentWorker,
):
    """Run headless LibreOffice conversion in the task container."""

    def __init__(
        self,
        *,
        workspace: ContainerWorkspace,
        executable: str,
        timeout_seconds: int,
    ) -> None:
        LibreOfficeBackend.__init__(
            self,
            executable=executable,
            timeout_seconds=timeout_seconds,
        )
        _ContainerDocumentWorker.__init__(
            self,
            workspace=workspace,
            timeout_seconds=timeout_seconds,
        )

    def convert(
        self,
        *,
        source: Path,
        output_dir: Path,
        output_format: str,
    ) -> LibreOfficeConversion:
        payload = self._call(
            {
                "operation": "convert",
                "source_path": self.workspace.to_container_path(source),
                "output_dir": self.workspace.to_container_path(output_dir),
                "output_format": output_format,
                "executable": self.executable,
                "timeout_seconds": self.timeout_seconds,
            },
            timeout_error="conversion_timeout",
            unavailable_error="backend_unavailable",
        )

        generated = payload.get("generated_path")
        backend = payload.get("backend")
        version = payload.get("backend_version")
        evidence = payload.get("execution_evidence", {})
        if (
            not isinstance(generated, str)
            or not isinstance(backend, str)
            or not isinstance(version, str)
            or not isinstance(evidence, dict)
        ):
            raise LibreOfficeFailure("invalid_backend_response")

        return LibreOfficeConversion(
            generated_path=self.workspace.to_host_path(generated),
            backend=backend,
            backend_version=version,
            stdout=str(payload.get("backend_stdout", ""))[-4000:],
            stderr=str(payload.get("backend_stderr", ""))[-4000:],
            execution_evidence=evidence,
        )
