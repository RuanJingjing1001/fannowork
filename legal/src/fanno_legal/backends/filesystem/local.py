from __future__ import annotations

from pathlib import Path

from fanno_work.backends import execution_evidence

from .base import (
    FilesystemBackendEntry,
    FilesystemBackendFailure,
)


class LocalFilesystemBackend:
    """Direct host filesystem backend used for fast local tests."""

    backend_id = "local-filesystem"

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace).resolve()

    @property
    def execution_evidence(self) -> dict[str, object]:
        return execution_evidence(
            backend=self.backend_id,
            execution_scope="controller_process",
            timeout_seconds=None,
            runtime="python",
        )

    def list_directory(self, relative_path: str) -> list[FilesystemBackendEntry]:
        directory = self._path(relative_path)
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except FileNotFoundError as exc:
            raise FilesystemBackendFailure("path_not_found") from exc
        except NotADirectoryError as exc:
            raise FilesystemBackendFailure("invalid_path") from exc
        except OSError as exc:
            raise FilesystemBackendFailure("permission_denied", str(exc)) from exc

        entries: list[FilesystemBackendEntry] = []
        for child in children:
            child_relative = child.relative_to(self.workspace).as_posix()
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
            entries.append(
                FilesystemBackendEntry(
                    path=child_relative,
                    kind=kind,
                    size=size,
                )
            )
        return entries

    def read_bytes(self, relative_path: str) -> bytes:
        try:
            return self._path(relative_path).read_bytes()
        except FileNotFoundError as exc:
            raise FilesystemBackendFailure("path_not_found") from exc
        except IsADirectoryError as exc:
            raise FilesystemBackendFailure("invalid_path") from exc
        except OSError as exc:
            raise FilesystemBackendFailure("permission_denied", str(exc)) from exc

    def write_bytes(self, relative_path: str, content: bytes) -> None:
        path = self._path(relative_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        except OSError as exc:
            raise FilesystemBackendFailure("write_failed", str(exc)) from exc

    def _path(self, relative_path: str) -> Path:
        if relative_path == ".":
            return self.workspace
        path = self.workspace.joinpath(*relative_path.split("/")).resolve()
        if not path.is_relative_to(self.workspace):
            raise FilesystemBackendFailure("invalid_path")
        return path
