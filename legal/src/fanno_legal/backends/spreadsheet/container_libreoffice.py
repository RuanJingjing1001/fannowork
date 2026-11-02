from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fanno_work.sandbox import (
    ContainerWorkspace,
    ContainerWorkspaceError,
)

from .libreoffice_calc import (
    LibreOfficeCalcBackend,
    LibreOfficeCalcFailure,
)


class ContainerLibreOfficeCalcBackend(
    LibreOfficeCalcBackend
):
    """Run the existing PyUNO Calc worker in the task container."""

    def __init__(
        self,
        *,
        workspace: ContainerWorkspace,
        system_python: str = "/usr/bin/python3",
        soffice_executable: str = "soffice",
        timeout_seconds: int = 60,
        worker_path: str = "/opt/fanno/workers/calc_worker.py",
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
        source_path = translated.get("source_path")
        if isinstance(source_path, str) and source_path:
            translated["source_path"] = (
                self.container_workspace
                .to_container_path(Path(source_path))
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
                "spreadsheet_timeout"
                if exc.code == "container_timeout"
                else "uno_backend_unavailable"
            )
            raise LibreOfficeCalcFailure(
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
            payload["execution_evidence"] = {
                "backend": "libreoffice-calc-uno",
                "execution_scope": "task_container",
                "timeout_seconds": self.timeout_seconds,
                "runtime": "docker",
            }
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
