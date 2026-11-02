"""Container-side worker for extraction and headless conversion."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

from fanno_work.backends import BackendFailure, execution_evidence

from fanno_legal.backends.document.libreoffice_cli import (
    LibreOfficeBackend,
)
from fanno_legal.backends.document.text_extractor import TextExtractor


def _required_string(
    request: dict[str, Any],
    key: str,
) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _positive_integer(
    request: dict[str, Any],
    key: str,
) -> int:
    value = request.get(key)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
    ):
        raise ValueError(f"{key} must be a positive integer")
    return value


def execute(request: dict[str, Any]) -> dict[str, Any]:
    operation = _required_string(request, "operation")
    source = Path(_required_string(request, "source_path"))

    if operation == "extract_text":
        maximum = _positive_integer(
            request,
            "max_archive_uncompressed_bytes",
        )
        result = TextExtractor(
            max_archive_uncompressed_bytes=maximum
        ).extract(source)
        return {
            "ok": True,
            "operation": operation,
            "text": result.text,
            "page_count": result.page_count,
            "backend": result.backend,
            "warnings": list(result.warnings),
            "execution_evidence": execution_evidence(
                backend=result.backend,
                execution_scope="task_container",
                timeout_seconds=_positive_integer(
                    request,
                    "timeout_seconds",
                ),
                runtime="docker",
            ),
        }

    if operation == "convert":
        output_dir = Path(
            _required_string(request, "output_dir")
        )
        output_format = _required_string(
            request,
            "output_format",
        )
        timeout_seconds = _positive_integer(
            request,
            "timeout_seconds",
        )
        backend = LibreOfficeBackend(
            executable=_required_string(
                request,
                "executable",
            ),
            timeout_seconds=timeout_seconds,
        )
        result = backend.convert(
            source=source,
            output_dir=output_dir,
            output_format=output_format,
        )
        return {
            "ok": True,
            "operation": operation,
            "generated_path": str(result.generated_path),
            "backend": result.backend,
            "backend_version": result.backend_version,
            "backend_stdout": result.stdout,
            "backend_stderr": result.stderr,
            "execution_evidence": execution_evidence(
                backend=result.backend,
                execution_scope="task_container",
                timeout_seconds=timeout_seconds,
                runtime="docker",
            ),
        }

    raise ValueError(f"unsupported operation: {operation}")


def main() -> int:
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        response = execute(request)
        status = 0
    except BackendFailure as exc:
        response = {
            "ok": False,
            "error": exc.code,
            "backend_error": exc.as_dict(),
        }
        status = 1
    except (OSError, ValueError) as exc:
        response = {
            "ok": False,
            "error": "invalid_worker_request",
            "backend_error": {
                "code": "invalid_worker_request",
                "detail": str(exc),
                "backend": "document-worker",
                "timed_out": False,
                "retryable": False,
            },
        }
        status = 1

    json.dump(
        response,
        sys.stdout,
        ensure_ascii=False,
        sort_keys=True,
    )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
