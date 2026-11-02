from __future__ import annotations

from typing import Any, Protocol

from fanno_work.backends import BackendFailure


class CalendarBackendFailure(BackendFailure):
    def __init__(
        self,
        code: str,
        detail: str = "",
        *,
        timed_out: bool = False,
        retryable: bool = False,
        backend: str = "radicale-caldav",
    ) -> None:
        super().__init__(
            code,
            detail,
            backend=backend,
            timed_out=timed_out,
            retryable=retryable,
        )


class CalendarBackend(Protocol):
    backend_id: str

    def list_calendars(self) -> dict[str, Any]: ...

    def list_events(
        self,
        *,
        calendar: str,
        start: str | None,
        end: str | None,
    ) -> dict[str, Any]: ...

    def find_events(
        self,
        *,
        calendar: str,
        query: str,
        max_results: int,
    ) -> dict[str, Any]: ...

    def create_event(
        self,
        *,
        calendar: str,
        uid: str,
        summary: str,
        starts_at: str,
        ends_at: str,
        description: str,
        location: str,
    ) -> dict[str, Any]: ...

    def delete_event(
        self,
        *,
        calendar: str,
        uid: str,
    ) -> dict[str, Any]: ...
