from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fanno_work.sandbox import (
    ContainerWorkspace,
    ContainerWorkspaceError,
)

from .libreoffice_uno import (
    LibreOfficeUnoBackend,
    LibreOfficeUnoFailure,
)


class ContainerLibreOfficeBackend(
    LibreOfficeUnoBackend
):
    """Run the existing PyUNO Writer worker in the task container."""

    def __init__(
        self,
        *,
        workspace: ContainerWorkspace,
        system_python: str = "/usr/bin/python3",
        soffice_executable: str = "soffice",
        timeout_seconds: int = 60,
        worker_path: str = "/opt/fanno/workers/uno_worker.py",
    ) -> None:
        super().__init__(
            system_python=system_python,
            soffice_executable=soffice_executable,
            timeout_seconds=timeout_seconds,
            worker_path=Path(worker_path),
        )
        self.container_workspace = workspace
        self.container_worker_path = worker_path

    def _execute_worker(
        self,
        request: dict[str, Any],
    ) -> tuple[str, str, int]:
        translated = dict(request)
        for key in ("source_path", "target_path"):
            value = translated.get(key)
            if isinstance(value, str) and value:
                translated[key] = (
                    self.container_workspace
                    .to_container_path(Path(value))
                )

        try:
            completed = self.container_workspace.execute(
                [
                    self.system_python,
                    self.container_worker_path,
                ],
                input_text=json.dumps(
                    translated,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                timeout_seconds=self.timeout_seconds + 15,
            )
        except ContainerWorkspaceError as exc:
            code = (
                "uno_timeout"
                if exc.code == "container_timeout"
                else "uno_backend_unavailable"
            )
            raise LibreOfficeUnoFailure(
                code,
                exc.detail or exc.code,
            ) from exc

        stdout = completed.stdout
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return (
                stdout,
                completed.stderr,
                completed.return_code,
            )

        if isinstance(payload, dict):
            target_path = payload.get(
                "target_path"
            )
            if isinstance(target_path, str):
                try:
                    payload["target_path"] = str(
                        self.container_workspace
                        .to_host_path(target_path)
                    )
                except ContainerWorkspaceError as exc:
                    raise LibreOfficeUnoFailure(
                        "edit_output_mismatch",
                        exc.detail or target_path,
                    ) from exc

            evidence = payload.get(
                "execution_evidence"
            )
            if not isinstance(evidence, dict):
                evidence = {}
            evidence.update(
                {
                    "backend": "libreoffice-writer-uno",
                    "execution_scope": (
                        "task_container"
                    ),
                    "timeout_seconds": self.timeout_seconds,
                    "runtime": "docker",
                }
            )
            payload["execution_evidence"] = evidence
            stdout = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
            )

        return (
            stdout,
            completed.stderr,
            completed.return_code,
        )
