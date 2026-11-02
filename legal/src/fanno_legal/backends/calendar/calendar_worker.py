from __future__ import annotations

import json
import sys
from typing import Any

from fanno_work.backends import execution_evidence
from fanno_legal.backends.calendar.base import CalendarBackendFailure
from fanno_legal.backends.calendar.radicale import RadicaleCalendarClient


def execute(request: dict[str, Any]) -> dict[str, Any]:
    operation = request.get("operation")
    timeout_seconds = int(request.get("timeout_seconds", 15))
    client = RadicaleCalendarClient(
        url=str(request.get("url", "http://radicale:5232")),
        username=str(request.get("username", "benchmark")),
        password=str(request.get("password", "")),
        timeout_seconds=timeout_seconds,
    )
    try:
        if operation == "list_calendars":
            calendars = client.list_calendars()
            output = {"calendars": calendars, "calendar_count": len(calendars)}
        elif operation == "list_events":
            events = client.list_events(
                calendar=str(request["calendar"]),
                start=request.get("start"),
                end=request.get("end"),
            )
            output = {"events": events, "event_count": len(events)}
        elif operation == "find_events":
            events = client.list_events(calendar=str(request["calendar"]))
            query = str(request["query"]).casefold()
            matches = [
                event
                for event in events
                if query in str(event.get("summary", "")).casefold()
                or query in str(event.get("description", "")).casefold()
                or query in str(event.get("location", "")).casefold()
            ][: int(request["max_results"])]
            output = {"events": matches, "event_count": len(matches)}
        elif operation == "create_event":
            event = client.create_event(
                calendar=str(request["calendar"]),
                uid=str(request["uid"]),
                summary=str(request["summary"]),
                starts_at=str(request["starts_at"]),
                ends_at=str(request["ends_at"]),
                description=str(request.get("description", "")),
                location=str(request.get("location", "")),
            )
            output = {"event": event, "created": True}
        elif operation == "delete_event":
            event = client.delete_event(
                calendar=str(request["calendar"]),
                uid=str(request["uid"]),
            )
            output = {"event": event, "deleted": True}
        else:
            raise CalendarBackendFailure("unknown_calendar_operation", str(operation))

        return {
            "ok": True,
            "output": output,
            "execution_evidence": execution_evidence(
                backend="radicale-caldav",
                execution_scope="task_container",
                timeout_seconds=timeout_seconds,
                runtime="docker",
            ),
        }
    finally:
        client.close()


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
        if not isinstance(request, dict):
            raise CalendarBackendFailure("invalid_backend_request")
        payload = execute(request)
        code = 0
    except CalendarBackendFailure as exc:
        payload = {
            "ok": False,
            "error": exc.code,
            "backend_error": exc.as_dict(),
        }
        code = 1
    except Exception as exc:
        payload = {
            "ok": False,
            "error": "calendar_backend_failed",
            "backend_error": {
                "code": "calendar_backend_failed",
                "detail": str(exc),
                "backend": "radicale-caldav",
                "timed_out": False,
                "retryable": False,
            },
        }
        code = 1
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
