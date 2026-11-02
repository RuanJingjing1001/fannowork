from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

from fanno_work.models import ActionRequest, ActionResult

from fanno_legal.backends.filesystem import (
    FilesystemBackend,
    FilesystemBackendFailure,
    LocalFilesystemBackend,
)
from fanno_legal.state.audit import AuditLog
from fanno_legal.state.database import StateDatabase


class FilesystemFailure(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class FilesystemCapability:
    ACTION_NAMES = frozenset(
        {
            "filesystem.list",
            "filesystem.read",
            "filesystem.write",
        }
    )

    def __init__(
        self,
        workspace: Path,
        database: StateDatabase,
        audit: AuditLog,
        config: dict[str, Any],
        backend: FilesystemBackend | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.database = database
        self.audit = audit

        readable = config.get("readable_prefixes")
        writable = config.get("writable_prefixes")
        max_file_bytes = config.get("max_file_bytes")

        if not isinstance(readable, list):
            raise ValueError(
                "filesystem.readable_prefixes "
                "must be a list"
            )
        if not isinstance(writable, list):
            raise ValueError(
                "filesystem.writable_prefixes "
                "must be a list"
            )
        if (
            not isinstance(max_file_bytes, int)
            or isinstance(max_file_bytes, bool)
            or max_file_bytes <= 0
        ):
            raise ValueError(
                "filesystem.max_file_bytes "
                "must be a positive integer"
            )

        self.readable_prefixes = tuple(
            self._normalize_prefix(prefix)
            for prefix in readable
        )
        self.writable_prefixes = tuple(
            self._normalize_prefix(prefix)
            for prefix in writable
        )
        self.max_file_bytes = max_file_bytes
        self.backend = backend or LocalFilesystemBackend(
            self.workspace
        )

    def act(
        self,
        actor: str,
        request: ActionRequest,
    ) -> ActionResult:
        version_before = (
            self.database.get_state_version()
        )

        try:
            result = self._dispatch(request)
        except (
            FilesystemFailure,
            FilesystemBackendFailure,
        ) as exc:
            result = ActionResult(
                ok=False,
                error=exc.code,
            )

        version_after = (
            self.database.get_state_version()
        )

        self.audit.append(
            actor=actor,
            action=request.name,
            arguments=self._audit_arguments(
                request
            ),
            ok=result.ok,
            error=result.error,
            state_version_before=version_before,
            state_version_after=version_after,
        )

        return result

    def _dispatch(
        self,
        request: ActionRequest,
    ) -> ActionResult:
        if request.name == "filesystem.list":
            return self._list(request.arguments)

        if request.name == "filesystem.read":
            return self._read(request.arguments)

        if request.name == "filesystem.write":
            return self._write(request.arguments)

        return ActionResult(
            ok=False,
            error="unknown_action",
        )

    def _list(
        self,
        arguments: dict[str, Any],
    ) -> ActionResult:
        requested_path = arguments.get("path")

        relative, directory = self._resolve_path(
            requested_path,
            allow_root=True,
        )

        if (
            relative != "."
            and not self._is_allowed(
                relative,
                self.readable_prefixes,
            )
        ):
            raise FilesystemFailure(
                "permission_denied"
            )

        if not directory.exists():
            raise FilesystemFailure(
                "path_not_found"
            )
        if not directory.is_dir():
            raise FilesystemFailure(
                "invalid_path"
            )

        try:
            entries = [
                {
                    "path": item.path,
                    "kind": item.kind,
                    "size": item.size,
                }
                for item in self.backend.list_directory(
                    relative
                )
            ]
        except FilesystemBackendFailure as exc:
            raise FilesystemFailure(exc.code) from exc

        return ActionResult(
            ok=True,
            output={
                "path": relative,
                "entries": entries,
                "backend": self.backend.backend_id,
                "execution_evidence": dict(
                    self.backend.execution_evidence
                ),
            },
            state_changed=False,
        )

    def _read(
        self,
        arguments: dict[str, Any],
    ) -> ActionResult:
        requested_path = arguments.get("path")
        relative, path = self.resolve_readable_file(
            requested_path
        )

        try:
            size = path.stat().st_size
        except OSError as exc:
            raise FilesystemFailure(
                "permission_denied"
            ) from exc

        if size > self.max_file_bytes:
            raise FilesystemFailure(
                "file_too_large"
            )

        try:
            content_bytes = self.backend.read_bytes(
                relative
            )
        except FilesystemBackendFailure as exc:
            raise FilesystemFailure(exc.code) from exc

        try:
            content = content_bytes.decode(
                "utf-8"
            )
        except UnicodeDecodeError as exc:
            raise FilesystemFailure(
                "unsupported_encoding"
            ) from exc

        return ActionResult(
            ok=True,
            output={
                "path": relative,
                "content": content,
                "size": len(content_bytes),
                "sha256": hashlib.sha256(
                    content_bytes
                ).hexdigest(),
                "backend": self.backend.backend_id,
                "execution_evidence": dict(
                    self.backend.execution_evidence
                ),
            },
            state_changed=False,
        )

    def resolve_readable_file(
        self,
        value: Any,
    ) -> tuple[str, Path]:
        """Resolve one file using the environment read policy."""
        relative, path = self._resolve_path(value)

        if not self._is_allowed(
            relative,
            self.readable_prefixes,
        ):
            raise FilesystemFailure("permission_denied")
        if not path.exists():
            raise FilesystemFailure("path_not_found")
        if not path.is_file():
            raise FilesystemFailure("invalid_path")

        return relative, path

    def resolve_writable_path(
        self,
        value: Any,
    ) -> tuple[str, Path]:
        """Resolve a path using the environment write policy."""
        relative, path = self._resolve_path(
            value
        )

        if not self._is_allowed(
            relative,
            self.writable_prefixes,
        ):
            raise FilesystemFailure(
                "permission_denied"
            )

        if path.exists() and not path.is_file():
            raise FilesystemFailure(
                "invalid_path"
            )

        return relative, path

    def _write(
        self,
        arguments: dict[str, Any],
    ) -> ActionResult:
        requested_path = arguments.get("path")
        content = arguments.get("content")

        if not isinstance(content, str):
            raise FilesystemFailure(
                "invalid_path"
            )

        relative, path = self._resolve_path(
            requested_path
        )

        if not self._is_allowed(
            relative,
            self.writable_prefixes,
        ):
            raise FilesystemFailure(
                "permission_denied"
            )

        if path.exists() and path.is_dir():
            raise FilesystemFailure(
                "invalid_path"
            )

        content_bytes = content.encode("utf-8")

        if len(content_bytes) > self.max_file_bytes:
            raise FilesystemFailure(
                "file_too_large"
            )

        existed = path.exists()

        if existed:
            try:
                existing_content = (
                    self.backend.read_bytes(
                        relative
                    )
                )
            except FilesystemBackendFailure as exc:
                raise FilesystemFailure(
                    "write_failed"
                ) from exc

            if existing_content == content_bytes:
                return ActionResult(
                    ok=True,
                    output={
                        "path": relative,
                        "size": len(content_bytes),
                        "sha256": hashlib.sha256(
                            content_bytes
                        ).hexdigest(),
                        "created": False,
                        "backend": self.backend.backend_id,
                        "execution_evidence": dict(
                            self.backend.execution_evidence
                        ),
                    },
                    state_changed=False,
                )

        try:
            self.backend.write_bytes(
                relative,
                content_bytes,
            )
        except FilesystemBackendFailure as exc:
            raise FilesystemFailure(exc.code) from exc

        self.database.advance_state_version()

        return ActionResult(
            ok=True,
            output={
                "path": relative,
                "size": len(content_bytes),
                "sha256": hashlib.sha256(
                    content_bytes
                ).hexdigest(),
                "created": not existed,
                "backend": self.backend.backend_id,
                "execution_evidence": dict(
                    self.backend.execution_evidence
                ),
            },
            state_changed=True,
        )

    def _resolve_path(
        self,
        value: Any,
        *,
        allow_root: bool = False,
    ) -> tuple[str, Path]:
        if not isinstance(value, str):
            raise FilesystemFailure(
                "invalid_path"
            )
        if not value or "\\" in value:
            raise FilesystemFailure(
                "invalid_path"
            )

        relative_path = PurePosixPath(value)

        if relative_path.is_absolute():
            raise FilesystemFailure(
                "invalid_path"
            )
        if ".." in relative_path.parts:
            raise FilesystemFailure(
                "invalid_path"
            )

        normalized = relative_path.as_posix()

        if normalized == ".":
            if not allow_root:
                raise FilesystemFailure(
                    "invalid_path"
                )
            return ".", self.workspace

        unresolved = self.workspace.joinpath(
            *relative_path.parts
        )

        current = self.workspace
        for part in relative_path.parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise FilesystemFailure(
                    "invalid_path"
                )

        resolved = unresolved.resolve()

        if not resolved.is_relative_to(
            self.workspace
        ):
            raise FilesystemFailure(
                "invalid_path"
            )

        return normalized, resolved

    @staticmethod
    def _normalize_prefix(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError(
                "filesystem prefix must be a string"
            )
        if not value or "\\" in value:
            raise ValueError(
                "filesystem prefix is invalid"
            )

        path = PurePosixPath(value)

        if (
            path.is_absolute()
            or ".." in path.parts
            or path.as_posix() == "."
        ):
            raise ValueError(
                "filesystem prefix is invalid"
            )

        return path.as_posix().rstrip("/") + "/"

    @staticmethod
    def _is_allowed(
        path: str,
        prefixes: tuple[str, ...],
    ) -> bool:
        return any(
            path == prefix.rstrip("/")
            or path.startswith(prefix)
            for prefix in prefixes
        )

    @staticmethod
    def _audit_arguments(
        request: ActionRequest,
    ) -> dict[str, Any]:
        arguments = dict(request.arguments)

        if request.name != "filesystem.write":
            return arguments

        content = arguments.pop("content", None)

        if isinstance(content, str):
            encoded = content.encode("utf-8")
            arguments["content_bytes"] = len(
                encoded
            )
            arguments["content_sha256"] = (
                hashlib.sha256(
                    encoded
                ).hexdigest()
            )
        else:
            arguments["content_type"] = type(
                content
            ).__name__

        return arguments
