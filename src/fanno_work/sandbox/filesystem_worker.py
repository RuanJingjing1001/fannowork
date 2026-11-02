"""Container-side worker for filesystem backend operations."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tempfile
from typing import Any


ROOT = Path("/workspace").resolve()


class WorkerError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def resolve_path(value: object, *, allow_root: bool = False) -> tuple[str, Path]:
    if not isinstance(value, str) or not value or "\\" in value:
        raise WorkerError("invalid_path")

    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts:
        raise WorkerError("invalid_path")

    normalized = pure.as_posix()
    if normalized == "." and not allow_root:
        raise WorkerError("invalid_path")

    current = ROOT
    for part in pure.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise WorkerError("invalid_path")

    resolved = ROOT if normalized == "." else ROOT.joinpath(*pure.parts).resolve()
    if not resolved.is_relative_to(ROOT):
        raise WorkerError("invalid_path")
    return normalized, resolved


def list_directory(request: dict[str, Any]) -> dict[str, Any]:
    relative, directory = resolve_path(request.get("path"), allow_root=True)
    if not directory.exists():
        raise WorkerError("path_not_found")
    if not directory.is_dir():
        raise WorkerError("invalid_path")

    entries: list[dict[str, Any]] = []
    for child in sorted(directory.iterdir(), key=lambda item: item.name):
        child_relative = child.relative_to(ROOT).as_posix()
        if child.is_symlink():
            kind = "symlink"
            size: int | None = child.lstat().st_size
        elif child.is_dir():
            kind = "directory"
            size = None
        elif child.is_file():
            kind = "file"
            size = child.stat().st_size
        else:
            kind = "other"
            size = None
        entries.append({"path": child_relative, "kind": kind, "size": size})

    return {"path": relative, "entries": entries}


def read_file(request: dict[str, Any]) -> dict[str, Any]:
    relative, path = resolve_path(request.get("path"))
    if not path.exists():
        raise WorkerError("path_not_found")
    if not path.is_file():
        raise WorkerError("invalid_path")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise WorkerError("permission_denied", str(exc)) from exc
    return {
        "path": relative,
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def write_file(request: dict[str, Any]) -> dict[str, Any]:
    relative, path = resolve_path(request.get("path"))
    encoded = request.get("content_base64")
    if not isinstance(encoded, str):
        raise WorkerError("invalid_request")
    try:
        content = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise WorkerError("invalid_request") from exc

    if path.exists() and not path.is_file():
        raise WorkerError("invalid_path")

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, path)
    except OSError as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise WorkerError("write_failed", str(exc)) from exc

    return {"path": relative, "size": len(content)}


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
        if not isinstance(request, dict):
            raise WorkerError("invalid_request")
        operation = request.get("operation")
        if operation == "list":
            output = list_directory(request)
        elif operation == "read":
            output = read_file(request)
        elif operation == "write":
            output = write_file(request)
        else:
            raise WorkerError("unknown_operation")
        response = {"ok": True, "operation": operation, "output": output}
        code = 0
    except WorkerError as exc:
        response = {"ok": False, "error": exc.code, "detail": exc.detail}
        code = 1
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        response = {"ok": False, "error": "invalid_request", "detail": str(exc)}
        code = 1

    sys.stdout.write(json.dumps(response, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
