from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from ..models import (
    ActionDescriptor,
    ActionRequest,
    ActionResult,
    EnvironmentSpec,
    Observation,
    TaskSpec,
)
from .base import WorkflowEnvironment


class WorkspaceEnvironment(WorkflowEnvironment):
    """Small reference adapter used to exercise the core protocol."""

    def __init__(self, spec: EnvironmentSpec):
        self.spec = spec
        self.task: TaskSpec | None = None
        self.run_dir: Path | None = None
        self.workspace: Path | None = None
        self.state_version = 0

    def prepare(self, task: TaskSpec, run_dir: Path) -> None:
        self.task = task
        self.run_dir = run_dir.resolve()
        self.workspace = self.run_dir / "workspace"
        if self.run_dir.exists() and any(self.run_dir.iterdir()):
            raise RuntimeError(f"run directory must be empty: {self.run_dir}")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._materialize()

    def _materialize(self) -> None:
        assert self.task is not None and self.workspace is not None
        if self.workspace.exists():
            shutil.rmtree(self.workspace)
        (self.workspace / "inputs").mkdir(parents=True)
        (self.workspace / "outputs").mkdir(parents=True)
        for artifact in self.task.inputs:
            source = (self.task.source_dir / artifact.source).resolve()
            target = self._safe_path(self.workspace / "inputs", artifact.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        self.state_version = 0

    @staticmethod
    def _safe_path(root: Path, relative: str) -> Path:
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root.resolve()):
            raise ValueError(f"path escapes workspace: {relative}")
        return candidate

    def observe(self) -> Observation:
        assert self.task is not None and self.workspace is not None
        files = [
            str(path.relative_to(self.workspace))
            for path in sorted(self.workspace.rglob("*"))
            if path.is_file()
        ]
        actions = (
            ActionDescriptor("list_files", "List visible workspace files"),
            ActionDescriptor("read_file", "Read a UTF-8 file", {"path": "string"}),
            ActionDescriptor(
                "write_file", "Write a UTF-8 output file", {"path": "string", "content": "string"}
            ),
        )
        return Observation(
            task_id=self.task.id,
            state_version=self.state_version,
            available_actions=actions,
            data={"instruction": self.task.instruction, "files": files},
        )

    def act(self, request: ActionRequest) -> ActionResult:
        assert self.workspace is not None
        try:
            if request.name == "list_files":
                return ActionResult(True, self.observe().data, state_changed=False)
            if request.name == "read_file":
                path = self._safe_path(self.workspace, str(request.arguments["path"]))
                return ActionResult(True, {"path": str(request.arguments["path"]), "content": path.read_text("utf-8")})
            if request.name == "write_file":
                relative = str(request.arguments["path"])
                if not relative.startswith("outputs/"):
                    raise ValueError("write_file is restricted to outputs/")
                path = self._safe_path(self.workspace, relative)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(request.arguments["content"]), encoding="utf-8")
                self.state_version += 1
                return ActionResult(True, {"path": relative}, state_changed=True)
            return ActionResult(False, error=f"unknown action: {request.name}")
        except (KeyError, OSError, ValueError) as exc:
            return ActionResult(False, error=str(exc))

    def snapshot(self) -> dict:
        assert self.workspace is not None
        files: dict[str, str] = {}
        for path in sorted(self.workspace.rglob("*")):
            if path.is_file():
                files[str(path.relative_to(self.workspace))] = hashlib.sha256(path.read_bytes()).hexdigest()
        encoded = json.dumps(files, sort_keys=True).encode()
        return {"state_version": self.state_version, "files": files, "digest": hashlib.sha256(encoded).hexdigest()}

    def reset(self) -> None:
        if self.task is None or self.workspace is None:
            raise RuntimeError("environment is not prepared")
        self._materialize()

    def close(self) -> None:
        return None
