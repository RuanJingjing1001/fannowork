from __future__ import annotations

from typing import Any

from fanno_work.backends import (
    execution_evidence,
)

from .radicale import (
    RadicaleCalendarClient,
)


class DirectRadicaleCalendarBackend:
    backend_id = "radicale-caldav"

    def __init__(
        self,
        *,
        url: str,
        username: str,
        password: str,
        timeout_seconds: int,
    ) -> None:
        self.url = url
        self.username = username
        self.password = password
        self.timeout_seconds = (
            timeout_seconds
        )

    def _client(
        self,
    ) -> RadicaleCalendarClient:
        return RadicaleCalendarClient(
            url=self.url,
            username=self.username,
            password=self.password,
            timeout_seconds=(
                self.timeout_seconds
            ),
        )

    def _evidence(self) -> dict[str, Any]:
        return execution_evidence(
            backend=self.backend_id,
            execution_scope="task_container",
            timeout_seconds=(
                self.timeout_seconds
            ),
            runtime="docker",
        )

    def list_calendars(
        self,
    ) -> dict[str, Any]:
        client = self._client()

        try:
            calendars = (
                client.list_calendars()
            )
        finally:
            client.close()

        return {
            "calendars": calendars,
            "calendar_count": len(
                calendars
            ),
            "backend": self.backend_id,
            "execution_evidence": (
                self._evidence()
            ),
        }

    def list_events(
        self,
        *,
        calendar: str,
        start: str | None,
        end: str | None,
    ) -> dict[str, Any]:
        client = self._client()

        try:
            events = client.list_events(
                calendar=calendar,
                start=start,
                end=end,
            )
        finally:
            client.close()

        return {
            "events": events,
            "event_count": len(events),
            "backend": self.backend_id,
            "execution_evidence": (
                self._evidence()
            ),
        }

    def find_events(
        self,
        *,
        calendar: str,
        query: str,
        max_results: int,
    ) -> dict[str, Any]:
        client = self._client()

        try:
            events = client.list_events(
                calendar=calendar
            )
        finally:
            client.close()

        normalized = query.casefold()

        matches = [
            event
            for event in events
            if (
                normalized
                in str(
                    event.get(
                        "summary",
                        "",
                    )
                ).casefold()
                or normalized
                in str(
                    event.get(
                        "description",
                        "",
                    )
                ).casefold()
                or normalized
                in str(
                    event.get(
                        "location",
                        "",
                    )
                ).casefold()
            )
        ][:max_results]

        return {
            "events": matches,
            "event_count": len(matches),
            "backend": self.backend_id,
            "execution_evidence": (
                self._evidence()
            ),
        }

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
    ) -> dict[str, Any]:
        client = self._client()

        try:
            event = client.create_event(
                calendar=calendar,
                uid=uid,
                summary=summary,
                starts_at=starts_at,
                ends_at=ends_at,
                description=description,
                location=location,
            )
        finally:
            client.close()

        return {
            "event": event,
            "created": True,
            "backend": self.backend_id,
            "execution_evidence": (
                self._evidence()
            ),
        }

    def delete_event(
        self,
        *,
        calendar: str,
        uid: str,
    ) -> dict[str, Any]:
        client = self._client()

        try:
            event = client.delete_event(
                calendar=calendar,
                uid=uid,
            )
        finally:
            client.close()

        return {
            "event": event,
            "deleted": True,
            "backend": self.backend_id,
            "execution_evidence": (
                self._evidence()
            ),
        }