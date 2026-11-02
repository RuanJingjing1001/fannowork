from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import subprocess
from typing import Mapping, Sequence
import uuid

from fanno_work.backends import BackendFailure


class ContainerWorkspaceError(BackendFailure):
    """Raised when a task container cannot be managed safely."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(
            code,
            detail,
            backend="docker",
            timed_out=code == "container_timeout",
            retryable=code in {
                "container_timeout",
                "container_engine_unavailable",
            },
        )


@dataclass(frozen=True)
class ContainerExecution:
    command: tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str


class ContainerWorkspace:
    """One isolated Docker container bound to one task workspace.

    The host owns the task files. The container receives a read-write mount
    at ``/workspace`` and a read-only overlay for ``/workspace/inputs``.
    Every software backend for the task can therefore operate on the same
    bytes without exposing host-absolute paths to the agent.
    """

    CONTAINER_ROOT = PurePosixPath("/workspace")

    def __init__(
        self,
        *,
        workspace: Path,
        image: str,
        docker_executable: str = "docker",
        network: str = "none",
        cpu_limit: float | None = 2.0,
        memory_limit: str | None = "2g",
        pids_limit: int | None = 256,
        default_timeout_seconds: int = 60,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.image = self._required_string(image, "image")
        self.docker_executable = self._required_string(
            docker_executable,
            "docker_executable",
        )
        self.network = self._required_string(network, "network")

        if cpu_limit is not None and cpu_limit <= 0:
            raise ValueError("cpu_limit must be positive or null")
        if memory_limit is not None and not memory_limit:
            raise ValueError("memory_limit must be non-empty or null")
        if pids_limit is not None and pids_limit <= 0:
            raise ValueError("pids_limit must be positive or null")
        if default_timeout_seconds <= 0:
            raise ValueError("default_timeout_seconds must be positive")

        self.cpu_limit = cpu_limit
        self.memory_limit = memory_limit
        self.pids_limit = pids_limit
        self.default_timeout_seconds = default_timeout_seconds
        self.environment = dict(environment or {})
        self.container_name: str | None = None

    @property
    def running(self) -> bool:
        return self.container_name is not None

    def start(self) -> None:
        if self.running:
            raise ContainerWorkspaceError(
                "container_already_started",
                self.container_name or "",
            )

        inputs = self.workspace / "inputs"
        outputs = self.workspace / "outputs"
        work = self.workspace / "work"
        inputs.mkdir(parents=True, exist_ok=True)
        outputs.mkdir(parents=True, exist_ok=True)
        work.mkdir(parents=True, exist_ok=True)

        self._check_engine()
        self._check_image()

        name = f"fanno-task-{uuid.uuid4().hex[:12]}"
        command = [
            self.docker_executable,
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "--network",
            self.network,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,size=512m",
            "--env",
            "HOME=/tmp/fanno-home",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--workdir",
            str(self.CONTAINER_ROOT),
            "--volume",
            f"{self.workspace}:{self.CONTAINER_ROOT}:rw",
            "--volume",
            f"{inputs}:{self.CONTAINER_ROOT / 'inputs'}:ro",
            "--volume",
            f"{outputs}:{self.CONTAINER_ROOT / 'outputs'}:rw",
        ]

        if hasattr(os, "getuid"):
            command.extend(
                ["--user", f"{os.getuid()}:{os.getgid()}"]
            )
        if self.cpu_limit is not None:
            command.extend(["--cpus", str(self.cpu_limit)])
        if self.memory_limit is not None:
            command.extend(["--memory", self.memory_limit])
        if self.pids_limit is not None:
            command.extend(["--pids-limit", str(self.pids_limit)])

        for key, value in sorted(self.environment.items()):
            command.extend(["--env", f"{key}={value}"])

        command.extend([self.image, "sleep", "infinity"])

        completed = self._run_host(
            command,
            timeout_seconds=30,
            error_code="container_start_failed",
        )
        if completed.returncode != 0:
            raise ContainerWorkspaceError(
                "container_start_failed",
                completed.stderr.strip() or completed.stdout.strip(),
            )

        self.container_name = name

    def stop(self) -> None:
        name = self.container_name
        if name is None:
            return

        self.container_name = None
        try:
            subprocess.run(
                [self.docker_executable, "rm", "--force", name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            # The container was started with --rm. Lifecycle cleanup should
            # never conceal the original task failure.
            return

    def __enter__(self) -> ContainerWorkspace:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    def execute(
        self,
        command: Sequence[str],
        *,
        input_text: str | None = None,
        cwd: str = "/workspace",
        timeout_seconds: int | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> ContainerExecution:
        name = self.container_name
        if name is None:
            raise ContainerWorkspaceError("container_not_started")
        if not command or not all(
            isinstance(item, str) and item for item in command
        ):
            raise ValueError("command must contain non-empty strings")

        self._validate_container_path(cwd)
        timeout = timeout_seconds or self.default_timeout_seconds
        if timeout <= 0:
            raise ValueError("timeout_seconds must be positive")

        docker_command = [self.docker_executable, "exec"]
        if input_text is not None:
            docker_command.append("--interactive")
        docker_command.extend(["--workdir", cwd])

        for key, value in sorted((environment or {}).items()):
            docker_command.extend(["--env", f"{key}={value}"])

        docker_command.extend(
            [
                name,
                "timeout",
                "--signal=TERM",
                "--kill-after=2",
                str(timeout),
                *command,
            ]
        )

        try:
            completed = subprocess.run(
                docker_command,
                input=input_text,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout + 10,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ContainerWorkspaceError("container_timeout") from exc
        except OSError as exc:
            raise ContainerWorkspaceError(
                "container_execution_failed",
                str(exc),
            ) from exc

        return ContainerExecution(
            command=tuple(command),
            return_code=int(completed.returncode),
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def to_container_path(self, host_path: Path) -> str:
        resolved = Path(host_path).resolve()
        try:
            relative = resolved.relative_to(self.workspace)
        except ValueError as exc:
            raise ContainerWorkspaceError(
                "path_outside_workspace",
                str(resolved),
            ) from exc

        return str(self.CONTAINER_ROOT.joinpath(*relative.parts))

    def to_host_path(self, container_path: str) -> Path:
        self._validate_container_path(container_path)
        relative = PurePosixPath(container_path).relative_to(
            self.CONTAINER_ROOT
        )
        resolved = self.workspace.joinpath(*relative.parts).resolve()
        if not resolved.is_relative_to(self.workspace):
            raise ContainerWorkspaceError(
                "path_outside_workspace",
                container_path,
            )
        return resolved

    def _check_engine(self) -> None:
        try:
            completed = subprocess.run(
                [self.docker_executable, "info"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ContainerWorkspaceError(
                "container_engine_unavailable",
                str(exc),
            ) from exc

        if completed.returncode != 0:
            raise ContainerWorkspaceError(
                "container_engine_unavailable",
                completed.stderr.strip() or completed.stdout.strip(),
            )

    def _check_image(self) -> None:
        completed = self._run_host(
            [self.docker_executable, "image", "inspect", self.image],
            timeout_seconds=10,
            error_code="container_image_unavailable",
        )
        if completed.returncode != 0:
            raise ContainerWorkspaceError(
                "container_image_unavailable",
                self.image,
            )

    def _run_host(
        self,
        command: list[str],
        *,
        timeout_seconds: int,
        error_code: str,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ContainerWorkspaceError(error_code, str(exc)) from exc

    @classmethod
    def _validate_container_path(cls, value: str) -> None:
        path = PurePosixPath(value)
        if not path.is_absolute() or ".." in path.parts:
            raise ContainerWorkspaceError("invalid_container_path", value)
        try:
            path.relative_to(cls.CONTAINER_ROOT)
        except ValueError as exc:
            raise ContainerWorkspaceError(
                "path_outside_workspace",
                value,
            ) from exc

    @staticmethod
    def _required_string(value: object, name: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")
        return value
