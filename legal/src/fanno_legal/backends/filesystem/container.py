from __future__ import annotations

import base64
import json

from fanno_work.sandbox import (
    ContainerWorkspace,
    ContainerWorkspaceError,
)
from fanno_work.backends import execution_evidence

from .base import (
    FilesystemBackendEntry,
    FilesystemBackendFailure,
)


class ContainerFilesystemBackend:
    """Filesystem operations executed inside the task Docker container."""

    backend_id = "container-filesystem"

    def __init__(
        self,
        workspace: ContainerWorkspace,
        *,
        python_executable: str = "python",
        worker_path: str = "/opt/fanno/workers/filesystem_worker.py",
        timeout_seconds: int | None = None,
    ) -> None:
        self.workspace = workspace
        self.python_executable = python_executable
        self.worker_path = worker_path
        self.timeout_seconds = (
            workspace.default_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    @property
    def execution_evidence(self) -> dict[str, object]:
        return execution_evidence(
            backend=self.backend_id,
            execution_scope="task_container",
            timeout_seconds=self.timeout_seconds,
            runtime="docker",
        )

    def list_directory(self, relative_path: str) -> list[FilesystemBackendEntry]:
        output = self._call("list", relative_path)
        raw_entries = output.get("entries")
        if not isinstance(raw_entries, list):
            raise FilesystemBackendFailure("invalid_backend_response")

        entries: list[FilesystemBackendEntry] = []
        for item in raw_entries:
            if not isinstance(item, dict):
                raise FilesystemBackendFailure("invalid_backend_response")
            path = item.get("path")
            kind = item.get("kind")
            size = item.get("size")
            if (
                not isinstance(path, str)
                or not isinstance(kind, str)
                or not (size is None or isinstance(size, int))
            ):
                raise FilesystemBackendFailure("invalid_backend_response")
            entries.append(FilesystemBackendEntry(path=path, kind=kind, size=size))
        return entries

    def read_bytes(self, relative_path: str) -> bytes:
        output = self._call("read", relative_path)
        encoded = output.get("content_base64")
        if not isinstance(encoded, str):
            raise FilesystemBackendFailure("invalid_backend_response")
        try:
            return base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise FilesystemBackendFailure("invalid_backend_response") from exc

    def write_bytes(self, relative_path: str, content: bytes) -> None:
        self._call(
            "write",
            relative_path,
            content_base64=base64.b64encode(content).decode("ascii"),
        )

    def _call(
        self,
        operation: str,
        relative_path: str,
        **arguments: object,
    ) -> dict[str, object]:
        request = {
            "operation": operation,
            "path": relative_path,
            **arguments,
        }
        try:
            completed = self.workspace.execute(
                [self.python_executable, self.worker_path],
                input_text=json.dumps(request, ensure_ascii=False, sort_keys=True),
                timeout_seconds=self.timeout_seconds,
            )
        except ContainerWorkspaceError as exc:
            raise FilesystemBackendFailure(exc.code, exc.detail) from exc

        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise FilesystemBackendFailure(
                "invalid_backend_response",
                completed.stdout[-2000:],
            ) from exc

        if not isinstance(payload, dict):
            raise FilesystemBackendFailure("invalid_backend_response")
        if completed.return_code != 0 or payload.get("ok") is not True:
            raise FilesystemBackendFailure(
                str(payload.get("error", "container_execution_failed")),
                str(payload.get("detail", completed.stderr)),
            )
        output = payload.get("output")
        if not isinstance(output, dict):
            raise FilesystemBackendFailure("invalid_backend_response")
        return output
