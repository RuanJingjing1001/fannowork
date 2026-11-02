from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from fanno_work.backends import BackendFailure


class FilesystemBackendFailure(BackendFailure):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(
            code,
            detail,
            backend="filesystem",
            timed_out=code == "container_timeout",
        )


@dataclass(frozen=True)
class FilesystemBackendEntry:
    path: str
    kind: str
    size: int | None


class FilesystemBackend(Protocol):
    backend_id: str

    @property
    def execution_evidence(self) -> dict[str, Any]:
        ...

    def list_directory(self, relative_path: str) -> list[FilesystemBackendEntry]:
        ...

    def read_bytes(self, relative_path: str) -> bytes:
        ...

    def write_bytes(self, relative_path: str, content: bytes) -> None:
        ...
