from __future__ import annotations

import json
from typing import Any

from fanno_work.sandbox import ContainerWorkspace, ContainerWorkspaceError

from .base import CalendarBackendFailure


class ContainerRadicaleCalendarBackend:
    backend_id = "radicale-caldav"

    def __init__(
        self,
        *,
        workspace: ContainerWorkspace,
        url: str = "http://radicale:5232",
        username: str = "benchmark",
        password: str = "",
        timeout_seconds: int = 15,
        worker_path: str = "/opt/fanno/workers/calendar_worker.py",
        python_executable: str = "python",
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.workspace = workspace
        self.url = url
        self.username = username
        self.password = password
        self.timeout_seconds = timeout_seconds
        self.worker_path = worker_path
        self.python_executable = python_executable

    def list_calendars(self) -> dict[str, Any]:
        return self._call({"operation": "list_calendars"})

    def list_events(
        self,
        *,
        calendar: str,
        start: str | None,
        end: str | None,
    ) -> dict[str, Any]:
        return self._call(
            {
                "operation": "list_events",
                "calendar": calendar,
                "start": start,
                "end": end,
            }
        )

    def find_events(
        self,
        *,
        calendar: str,
        query: str,
        max_results: int,
    ) -> dict[str, Any]:
        return self._call(
            {
                "operation": "find_events",
                "calendar": calendar,
                "query": query,
                "max_results": max_results,
            }
        )

    def create_event(self, **arguments: Any) -> dict[str, Any]:
        return self._call({"operation": "create_event", **arguments})

    def delete_event(self, **arguments: Any) -> dict[str, Any]:
        return self._call({"operation": "delete_event", **arguments})

    def _call(self, arguments: dict[str, Any]) -> dict[str, Any]:
        request = {
            **arguments,
            "url": self.url,
            "username": self.username,
            "password": self.password,
            "timeout_seconds": self.timeout_seconds,
        }
        try:
            completed = self.workspace.execute(
                [self.python_executable, self.worker_path],
                input_text=json.dumps(request, ensure_ascii=False, sort_keys=True),
                timeout_seconds=self.timeout_seconds + 5,
            )
        except ContainerWorkspaceError as exc:
            timed_out = exc.code == "container_timeout"
            raise CalendarBackendFailure(
                "calendar_timeout" if timed_out else "calendar_backend_unavailable",
                exc.detail or exc.code,
                timed_out=timed_out,
                retryable=True,
            ) from exc

        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise CalendarBackendFailure(
                "invalid_backend_response",
                completed.stdout[-2000:],
            ) from exc
        if not isinstance(payload, dict):
            raise CalendarBackendFailure("invalid_backend_response")
        if completed.return_code != 0 or payload.get("ok") is not True:
            backend_error = payload.get("backend_error", {})
            detail = (
                str(backend_error.get("detail", ""))
                if isinstance(backend_error, dict)
                else completed.stderr[-2000:]
            )
            raise CalendarBackendFailure(
                str(payload.get("error", "calendar_backend_failed")),
                detail,
            )
        output = payload.get("output")
        evidence = payload.get("execution_evidence")
        if not isinstance(output, dict) or not isinstance(evidence, dict):
            raise CalendarBackendFailure("invalid_backend_response")
        return {
            **output,
            "backend": self.backend_id,
            "execution_evidence": dict(evidence),
        }
