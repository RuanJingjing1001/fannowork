from __future__ import annotations

from datetime import datetime
import hashlib
from typing import Any

from fanno_work.models import ActionRequest, ActionResult
from fanno_legal.backends.calendar import CalendarBackend, CalendarBackendFailure
from fanno_legal.state.audit import AuditLog
from fanno_legal.state.database import StateDatabase


class CalendarFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CalendarCapability:
    ACTION_NAMES = frozenset(
        {
            "calendar.list_calendars",
            "calendar.list_events",
            "calendar.find_events",
            "calendar.create_event",
            "calendar.delete_event",
        }
    )

    def __init__(
        self,
        *,
        backend: CalendarBackend,
        database: StateDatabase,
        audit: AuditLog,
        config: dict[str, Any],
    ) -> None:
        self.backend = backend
        self.database = database
        self.audit = audit
        self.max_results = self._positive_integer(config, "max_results")
        self.max_summary_characters = self._positive_integer(
            config, "max_summary_characters"
        )
        self.max_description_characters = self._positive_integer(
            config, "max_description_characters"
        )
        self.max_query_characters = self._positive_integer(
            config, "max_query_characters"
        )

    def act(self, actor: str, request: ActionRequest) -> ActionResult:
        version_before = self.database.get_state_version()
        try:
            result = self._dispatch(request)
        except (CalendarFailure, CalendarBackendFailure) as exc:
            result = ActionResult(ok=False, error=exc.code)
        version_after = self.database.get_state_version()
        self.audit.append(
            actor=actor,
            action=request.name,
            arguments=self._audit_arguments(request),
            ok=result.ok,
            error=result.error,
            state_version_before=version_before,
            state_version_after=version_after,
        )
        return result

    def _dispatch(self, request: ActionRequest) -> ActionResult:
        if request.name == "calendar.list_calendars":
            return ActionResult(
                ok=True,
                output=self.backend.list_calendars(),
                state_changed=False,
            )
        if request.name == "calendar.list_events":
            return self._list_events(request.arguments)
        if request.name == "calendar.find_events":
            return self._find_events(request.arguments)
        if request.name == "calendar.create_event":
            return self._create_event(request.arguments)
        if request.name == "calendar.delete_event":
            return self._delete_event(request.arguments)
        return ActionResult(ok=False, error="unknown_action")

    def _list_events(self, arguments: dict[str, Any]) -> ActionResult:
        calendar = self._required_string(arguments, "calendar", "invalid_calendar")
        start = arguments.get("start")
        end = arguments.get("end")
        if (start is None) != (end is None):
            raise CalendarFailure("invalid_time_range")
        if start is not None:
            self._datetime(start)
            self._datetime(end)
            if self._datetime(end) <= self._datetime(start):
                raise CalendarFailure("invalid_time_range")
        return ActionResult(
            ok=True,
            output=self.backend.list_events(
                calendar=calendar,
                start=start,
                end=end,
            ),
            state_changed=False,
        )

    def _find_events(self, arguments: dict[str, Any]) -> ActionResult:
        calendar = self._required_string(arguments, "calendar", "invalid_calendar")
        query = self._required_string(arguments, "query", "invalid_query")
        if len(query) > self.max_query_characters:
            raise CalendarFailure("query_too_large")
        max_results = arguments.get("max_results", self.max_results)
        if (
            not isinstance(max_results, int)
            or isinstance(max_results, bool)
            or max_results <= 0
        ):
            raise CalendarFailure("invalid_result_limit")
        return ActionResult(
            ok=True,
            output=self.backend.find_events(
                calendar=calendar,
                query=query,
                max_results=min(max_results, self.max_results),
            ),
            state_changed=False,
        )

    def _create_event(self, arguments: dict[str, Any]) -> ActionResult:
        calendar = self._required_string(arguments, "calendar", "invalid_calendar")
        uid = self._required_string(arguments, "uid", "invalid_uid")
        summary = self._required_string(arguments, "summary", "invalid_summary")
        starts_at = self._required_string(arguments, "starts_at", "invalid_datetime")
        ends_at = self._required_string(arguments, "ends_at", "invalid_datetime")
        description = arguments.get("description", "")
        location = arguments.get("location", "")
        if not isinstance(description, str) or not isinstance(location, str):
            raise CalendarFailure("invalid_event_content")
        if len(summary) > self.max_summary_characters:
            raise CalendarFailure("summary_too_large")
        if len(description) > self.max_description_characters:
            raise CalendarFailure("description_too_large")
        start_value = self._datetime(starts_at)
        end_value = self._datetime(ends_at)
        if end_value <= start_value:
            raise CalendarFailure("invalid_time_range")

        output = self.backend.create_event(
            calendar=calendar,
            uid=uid,
            summary=summary,
            starts_at=starts_at,
            ends_at=ends_at,
            description=description,
            location=location,
        )
        self.database.advance_state_version()
        return ActionResult(ok=True, output=output, state_changed=True)

    def _delete_event(self, arguments: dict[str, Any]) -> ActionResult:
        calendar = self._required_string(arguments, "calendar", "invalid_calendar")
        uid = self._required_string(arguments, "uid", "invalid_uid")
        output = self.backend.delete_event(calendar=calendar, uid=uid)
        self.database.advance_state_version()
        return ActionResult(ok=True, output=output, state_changed=True)

    @staticmethod
    def _datetime(value: Any) -> datetime:
        if not isinstance(value, str) or not value:
            raise CalendarFailure("invalid_datetime")
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise CalendarFailure("invalid_datetime") from exc

    @staticmethod
    def _required_string(
        arguments: dict[str, Any],
        name: str,
        error: str,
    ) -> str:
        value = arguments.get(name)
        if not isinstance(value, str) or not value:
            raise CalendarFailure(error)
        return value

    @staticmethod
    def _positive_integer(config: dict[str, Any], name: str) -> int:
        value = config.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"calendar.{name} must be a positive integer")
        return value

    @staticmethod
    def _audit_arguments(request: ActionRequest) -> dict[str, Any]:
        arguments = dict(request.arguments)
        description = arguments.pop("description", None)
        if isinstance(description, str):
            arguments["description_character_count"] = len(description)
            arguments["description_sha256"] = hashlib.sha256(
                description.encode("utf-8")
            ).hexdigest()
        return arguments
